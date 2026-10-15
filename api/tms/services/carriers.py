"""Freight going with outside carriers. A consignment is booked on a
carrier's manifest for a day (the carrier bay), its con note typed or
scanned and put right until the carrier collects it.

Every change takes the planning lock first, as planning runs does, so a
consignment is on one run or with one carrier, never both, however many
people are at it; the partial unique indexes on booking stand behind it.
Version one books by hand: see carrier_adapters for what comes later."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import Booking, Carrier, Consignment, ConsignmentEvent, Manifest, OutboundCall
from tms.services import runs
from tms.services.runs import lock_planning

# as the carrier prints it: letters, digits and dashes, 4 to 40 of them
CON_NOTE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,39}$")
# what may go on the carrier bay: from the tray, off a planned run, or off
# another carrier's bay
BOOKABLE = ("packed", "failed", "assigned", "booked")


def _now() -> datetime:
    return datetime.now(UTC)


def normal_con_note(raw: str) -> str:
    """Typed or scanned: spaces around it go, letters become capitals. Any
    other space, or anything but letters, digits and dashes, is refused
    rather than guessed at."""
    note = raw.strip().upper()
    if not CON_NOTE.match(note):
        raise FieldError("con_note", "A con note is 4 to 40 letters, digits and dashes, as on "
                                     "the carrier's label")
    return note


def _event(c: Consignment, kind: str, **detail) -> None:
    c.events.append(ConsignmentEvent(kind=kind, at=_now(), detail=detail))


def get_manifest(db: Session, manifest_id: int) -> Manifest:
    m = db.execute(
        select(Manifest).where(Manifest.id == manifest_id)
        .options(selectinload(Manifest.lines).selectinload(Booking.consignment)
                 .selectinload(Consignment.packages), selectinload(Manifest.carrier))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if m is None:
        raise NotFound(f"no manifest {manifest_id}")
    return m


def _open(db: Session, manifest_id: int) -> Manifest:
    """The manifest, read after the lock, and not collected yet."""
    m = get_manifest(db, manifest_id)
    if m.collected_at is not None:
        raise Conflict("manifest_collected",
                       f"{m.carrier.name} has collected {m.number}; what is on it can't change")
    return m


def _live_booking(db: Session, consignment_id: int) -> Booking | None:
    return db.execute(select(Booking).where(
        Booking.consignment_id == consignment_id, Booking.removed_at.is_(None))
        .execution_options(populate_existing=True)).scalar_one_or_none()


def take_off_bay(db: Session, c: Consignment, by: str) -> None:
    """Off the carrier bay, if it is on it, with a history row. The caller
    holds the planning lock and says where it goes next."""
    booking = _live_booking(db, c.id)
    if booking is None:
        return
    m = db.get(Manifest, booking.manifest_id)
    booking.removed_at = _now()
    _event(c, "unbooked", carrier=m.carrier.name, manifest=m.number, manifest_id=m.id, by=by)
    db.flush()


def _con_note_free(db: Session, carrier: Carrier, note: str, booking: Booking) -> None:
    """A con note is one consignment's with a carrier for good: refused while
    another consignment has it on the bay, and after, once it has been taken
    off or collected (its booking keeps it). The same consignment back on the
    bay, or moved to another day, may have it again; one put right is free
    (no booking keeps it). The caller holds the planning lock, so two
    consignments given the same one at once can't both have it."""
    taken = db.execute(select(Booking).where(
        Booking.carrier_id == carrier.id, Booking.con_note == note,
        Booking.consignment_id != booking.consignment_id)
        .order_by(Booking.removed_at.is_not(None), Booking.id.desc()).limit(1)).scalar_one_or_none()
    if taken is not None:
        other = taken.consignment
        raise FieldError("con_note", f"{note} is already the con note of {other.wms_ref} "
                                     f"({other.customer}) with {carrier.name}")


def _set_con_note(db: Session, carrier: Carrier, booking: Booking, note: str, by: str) -> None:
    """The same again is nothing; a new one is a history row naming the old."""
    if booking.con_note == note:
        return
    _con_note_free(db, carrier, note, booking)
    was, booking.con_note = booking.con_note, note
    _event(booking.consignment, "con_note", carrier=carrier.name, carrier_id=carrier.id,
           con_note=note, was=was, by=by)
    db.flush()


def _manifest_for(db: Session, carrier: Carrier, day: date, by: str) -> Manifest:
    """The carrier's open manifest for the day, made if there is none yet."""
    m = db.execute(select(Manifest).where(
        Manifest.carrier_id == carrier.id, Manifest.day == day,
        Manifest.collected_at.is_(None))).scalar_one_or_none()
    if m is None:
        m = Manifest(carrier_id=carrier.id, day=day, created_by=by)
        db.add(m)
        db.flush()
    return m


def book(db: Session, carrier_id: int, consignment_id: int, day: date, con_note: str | None,
         by: str) -> Manifest:
    """Put a consignment on the carrier's manifest for the day, from the
    tray, off a planned run or off another carrier's bay, and set its con
    note if one is given. Moved to another day with the same carrier it
    keeps its con note; to another carrier it doesn't (a con note is one
    carrier's). Asking for what is already so changes nothing. A refusal
    refuses the whole: nothing is moved."""
    lock_planning(db)
    carrier = db.get(Carrier, carrier_id)
    if carrier is None:
        raise NotFound(f"no carrier {carrier_id}")
    note = normal_con_note(con_note) if con_note is not None else None
    c = db.execute(select(Consignment).where(Consignment.id == consignment_id)
                   .execution_options(populate_existing=True)).scalar_one_or_none()
    if c is None:
        raise FieldError("consignment_id", f"no consignment {consignment_id}")
    if c.status not in BOOKABLE:
        raise Conflict("not_plannable",
                       f"{runs.gone(c)}, so it can't go with a carrier")
    booking = _live_booking(db, c.id)
    if booking is not None and booking.carrier_id == carrier.id \
            and db.get(Manifest, booking.manifest_id).day == day:
        m = db.get(Manifest, booking.manifest_id)
    else:
        if not carrier.active:
            raise Conflict("carrier_inactive",
                           f"{carrier.name} is deactivated, so nothing more can be booked with it")
        keep = booking.con_note if booking is not None and booking.carrier_id == carrier.id else None
        take_off_bay(db, c, by)
        runs.take_off_run(db, c, by)
        m = _manifest_for(db, carrier, day, by)
        booking = Booking(manifest_id=m.id, carrier_id=carrier.id, consignment_id=c.id,
                          con_note=keep, added_by=by)
        db.add(booking)
        c.status = "booked"
        _event(c, "booked", carrier=carrier.name, carrier_id=carrier.id, manifest=m.number,
               manifest_id=m.id, by=by)
        db.flush()
    if note is not None:
        _set_con_note(db, carrier, booking, note, by)
    return m


def unbook(db: Session, manifest_id: int, consignment_id: int, by: str) -> Manifest:
    """Back to the tray. Not on this manifest already: nothing to do."""
    lock_planning(db)
    m = _open(db, manifest_id)
    booking = next((b for b in m.lines if b.consignment_id == consignment_id), None)
    if booking is None:
        return m
    c = booking.consignment
    take_off_bay(db, c, by)
    c.status = "packed"
    db.flush()
    return m


def set_con_note(db: Session, manifest_id: int, consignment_id: int, con_note: str,
                 by: str) -> Manifest:
    """Typed or scanned, or put right, until the carrier collects it."""
    lock_planning(db)
    m = _open(db, manifest_id)
    note = normal_con_note(con_note)
    booking = next((b for b in m.lines if b.consignment_id == consignment_id), None)
    if booking is None:
        raise NotFound(f"{m.number} has no consignment {consignment_id}")
    _set_con_note(db, m.carrier, booking, note, by)
    return m


def refuse_missing_con_notes(m: Manifest) -> None:
    """The carrier's driver signs for each consignment by its con note."""
    missing = [b.consignment for b in m.lines if b.con_note is None]
    if missing:
        c = missing[0]
        raise Conflict("con_note_missing",
                       f"Add the con note of {c.customer} ({c.wms_ref}) first: each consignment "
                       "needs one before the carrier takes it")


def collect(db: Session, manifest_id: int, consignment_ids: list[int], by: str) -> Manifest:
    """The carrier has called and taken what is on the manifest: each
    consignment is collected, and a ship call to Simple WMS waits in the
    queue for each, with the carrier's name and the con note, in the same
    transaction, so none is lost and none is made twice. Only what the
    dispatcher saw (`consignment_ids`), each with its con note. A manifest
    already collected with just those on it is left as it is."""
    lock_planning(db)
    m = get_manifest(db, manifest_id)
    on_it = {b.consignment_id for b in m.lines}
    if m.collected_at is not None:
        if set(consignment_ids) == on_it:
            return m
        raise Conflict("manifest_collected",
                       f"{m.carrier.name} has collected {m.number}; what is on it can't change")
    if not m.lines:
        raise Conflict("manifest_empty", f"{m.number} has nothing on it")
    if len(consignment_ids) != len(set(consignment_ids)) or set(consignment_ids) != on_it:
        raise Conflict("stale", f"{m.number} changed while you were looking; it has been "
                                "reloaded, so check it and try again")
    refuse_missing_con_notes(m)
    for b in m.lines:
        runs.refuse_if_not_back(db, b.consignment, "the carrier can take it")
    now = _now()
    for b in m.lines:
        c = b.consignment
        c.status = "collected"
        _event(c, "collected", carrier=m.carrier.name, con_note=b.con_note, manifest=m.number,
               manifest_id=m.id, by=by)
        message_id = uuid.uuid4()
        db.add(OutboundCall(
            message_id=message_id, kind="ship", manifest_id=m.id, consignment_id=c.id,
            path=f"/v1/deliveries/{quote(c.wms_ref, safe='')}/ship",
            body={"message_id": str(message_id), "owner": c.owner, "carrier": m.carrier.name,
                  "tracking_no": b.con_note, "shipped_by": by[:64]},
            status="pending", attempts=0, next_attempt_at=now))
    m.collected_at = now
    m.collected_by = by
    db.flush()
    return m
