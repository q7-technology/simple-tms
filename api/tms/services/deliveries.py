"""The driver's side of a run: start it, deliver each stop or say why it
couldn't be, and finish it. Each change locks its run's row first, so two
confirmations for one run (two phones, or a retry racing the original) take
turns, and a stop is recorded once."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, FieldError, NotFound, violated_constraint
from tms.models import ConsignmentEvent, Pod, PodFile, PrestartItem, Run, Stop
from tms.services import runs
from tms.services.envelope import sender_of

MICRO = Decimal("0.000001")
TENTH = Decimal("0.1")
# a picture belongs to one stop: the database's word for it, and which picture
FILE_CONSTRAINTS = {"uq_pod_signature_file_id": "signature", "uq_pod_photo_file_id": "photo"}


@dataclass(frozen=True)
class Where:
    latitude: Decimal
    longitude: Decimal
    accuracy_m: Decimal | None


def own_run(db: Session, who, run_id: int) -> Run:
    """The run, if the caller may act on it: a driver only on their own (any
    other is not found, so run numbers can't be tried); a key or a user with
    the scope, on any."""
    run = runs.get_run(db, run_id)
    if who.kind == "driver" and run.driver_id != who.id:
        raise NotFound(f"no run {run_id}")
    return run


def driver_runs(db: Session, driver_id: int, day: date) -> list[Run]:
    """This driver's runs for the day, and any of theirs from before that
    left and were never finished."""
    ids = db.execute(
        select(Run.id).where(Run.driver_id == driver_id, or_(
            and_(Run.day == day, Run.status != "cancelled"),
            and_(Run.day < day, Run.status == "departed")))
        .order_by(Run.day, Run.id)).scalars().all()
    return [runs.get_run(db, i) for i in ids]


def start(db: Session, who, run_id: int) -> Run:
    """The driver is leaving: a planned run departs exactly as the
    dispatcher's "Leaves now" does (ship calls and all), once the driver has
    done the pre-start of its truck; one that has already left is left as it
    is."""
    run = own_run(db, who, run_id)
    if run.status == "done":
        raise Conflict("run_done", f"{run.number} is finished")
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")
    # the truck and its check are judged under the planning lock, so a change
    # of truck a moment before is seen
    return runs.depart(db, run_id, by=who.name, need_prestart=True)


def _locked(db: Session, who, run_id: int) -> Run:
    db.execute(select(Run.id).where(Run.id == run_id).with_for_update())
    return own_run(db, who, run_id)


def _on_the_road(run: Run) -> None:
    if run.status == "planned":
        raise Conflict("run_not_started", f"{run.number} hasn't left yet: start it first")
    if run.status == "done":
        raise Conflict("run_done", f"{run.number} is finished")
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")


def _open_stop(db: Session, run: Run, consignment_id: int) -> Stop:
    stop = db.execute(
        select(Stop).where(Stop.run_id == run.id, Stop.consignment_id == consignment_id,
                           Stop.removed_at.is_(None))
        .execution_options(populate_existing=True)).scalar_one_or_none()
    if stop is None:
        raise NotFound(f"{run.number} has no stop for consignment {consignment_id}")
    if stop.outcome is not None:
        said = "delivered" if stop.outcome == "delivered" else "not delivered"
        raise Conflict("stop_done",
                       f"stop {stop.sequence} on {run.number} is already recorded as {said}")
    return stop


def own_file(db: Session, who, file_id: uuid.UUID | None, kind: str) -> PodFile | None:
    """A picture this sender sent, not yet named by a stop or a pre-start item."""
    if file_id is None:
        return None
    row = db.get(PodFile, file_id)
    if row is None or row.uploaded_by != sender_of(who) or row.kind != kind:
        raise FieldError(f"{kind}_file", f"no {kind} {file_id} has been sent")
    used = db.execute(select(Pod.id).where(
        or_(Pod.signature_file_id == file_id, Pod.photo_file_id == file_id))).first()
    if used is not None:
        raise FieldError(f"{kind}_file", f"{kind} {file_id} already belongs to another stop")
    if db.execute(select(PrestartItem.id).where(PrestartItem.photo_file_id == file_id)).first():
        raise FieldError(f"{kind}_file", f"{kind} {file_id} already belongs to a pre-start check")
    return row


def _record(db: Session, who, run: Run, stop: Stop, *, message_id: uuid.UUID, outcome: str,
            at: datetime, where: Where | None, detail: dict, **pod) -> Pod:
    """The pod, the stop's outcome, the consignment's status and a history
    row, together. The time is the phone's, but never later than now."""
    when = min(at, datetime.now(UTC))
    row = Pod(message_id=message_id, run_id=run.id, stop_id=stop.id,
              consignment_id=stop.consignment_id, outcome=outcome, at=when,
              latitude=where.latitude.quantize(MICRO) if where else None,
              longitude=where.longitude.quantize(MICRO) if where else None,
              accuracy_m=(where.accuracy_m.quantize(TENTH)
                          if where and where.accuracy_m is not None else None),
              recorded_by=who.name, **pod)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as e:
        # another stop took the picture between _file's check and this insert
        name = violated_constraint(e)
        kind = FILE_CONSTRAINTS.get(name or "")
        if kind is None:
            raise
        db.rollback()
        file_id = pod["signature_file_id" if kind == "signature" else "photo_file_id"]
        raise FieldError(f"{kind}_file", f"{kind} {file_id} already belongs to another stop") from None
    stop.outcome = outcome
    stop.outcome_at = when
    c = stop.consignment
    c.status = outcome
    # the position stays on the pod only: history is kept for good, positions are not
    c.events.append(ConsignmentEvent(kind=outcome, at=when, detail={
        "run": run.number, "run_id": run.id, "pod_id": row.id, **detail, "by": who.name}))
    db.flush()
    return row


def deliver(db: Session, who, run_id: int, consignment_id: int, *, message_id: uuid.UUID,
            at: datetime, packages: list[dict], signed_name: str | None,
            signature_file: uuid.UUID | None, photo_file: uuid.UUID | None,
            note: str | None, where: Where | None) -> tuple[Run, Stop, Pod]:
    run = _locked(db, who, run_id)
    _on_the_road(run)
    stop = _open_stop(db, run, consignment_id)
    expected = sorted(p.package_no for p in stop.consignment.packages)
    if sorted(p["package_no"] for p in packages) != expected:
        raise FieldError("packages", f"name each of the {len(expected)} packages once")
    signature = own_file(db, who, signature_file, "signature")
    photo = own_file(db, who, photo_file, "photo")
    pod = _record(db, who, run, stop, message_id=message_id, outcome="delivered", at=at,
                  where=where, packages=packages, signed_name=signed_name, note=note,
                  signature_file_id=signature.id if signature else None,
                  photo_file_id=photo.id if photo else None,
                  detail={"packages": len(packages), "signed_name": signed_name,
                          "signature_file": str(signature.id) if signature else None,
                          "photo_file": str(photo.id) if photo else None})
    return run, stop, pod


def fail(db: Session, who, run_id: int, consignment_id: int, *, message_id: uuid.UUID,
         at: datetime, reason: str, note: str | None, photo_file: uuid.UUID | None,
         where: Where | None) -> tuple[Run, Stop, Pod]:
    """Couldn't deliver: the consignment stays on the truck and comes back to
    the depot. It is `failed` until a dispatcher puts it on another run."""
    run = _locked(db, who, run_id)
    _on_the_road(run)
    stop = _open_stop(db, run, consignment_id)
    photo = own_file(db, who, photo_file, "photo")
    pod = _record(db, who, run, stop, message_id=message_id, outcome="failed", at=at,
                  where=where, reason=reason, note=note, packages=[],
                  photo_file_id=photo.id if photo else None,
                  detail={"reason": reason, "note": note,
                          "photo_file": str(photo.id) if photo else None})
    return run, stop, pod


def tally(db: Session, run_id: int) -> dict[str | None, int]:
    """How many of a run's stops are delivered, failed, and still to go (None)."""
    rows = db.execute(select(Stop.outcome, func.count()).where(
        Stop.run_id == run_id, Stop.removed_at.is_(None)).group_by(Stop.outcome)).all()
    return {outcome: n for outcome, n in rows}


def finish(db: Session, who, run_id: int) -> Run:
    """Back at the depot: every stop is delivered or has a reason it wasn't.
    Finishing a finished run changes nothing."""
    run = _locked(db, who, run_id)
    if run.status == "done":
        return run
    _on_the_road(run)
    left = tally(db, run.id).get(None, 0)
    if left:
        raise Conflict("stops_left", f"{run.number} still has {left} "
                                     f"{'stop' if left == 1 else 'stops'} to deliver or explain")
    run.status = "done"
    run.finished_at = datetime.now(UTC)
    db.flush()
    return run
