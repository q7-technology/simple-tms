"""Planning runs: which consignments go on which truck, in what order.

Every change to a plan takes one lock first (a Postgres advisory lock held to
the end of the transaction), then reads what it changes. Two dispatchers
dragging at once are served one after the other, each seeing the other's
work, so a sequence never has a gap or a twin and a consignment is never on
two runs. Planning changes take milliseconds and a depot has a handful of
dispatchers, so one lock costs nothing, and with one lock there is no order
to get wrong and no deadlock. The partial unique indexes on run and stop
stand behind it."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from urllib.parse import quote

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import (
    Consignment, ConsignmentEvent, Driver, OutboundCall, Run, Stop, Vehicle,
)
from tms.services import fleet
from tms.services.load import Load, consignment_totals, load, total

PLANNING_LOCK = 7_302_201


def lock_planning(db: Session) -> None:
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": PLANNING_LOCK})


def get_run(db: Session, run_id: int) -> Run:
    run = db.execute(
        select(Run).where(Run.id == run_id)
        .options(selectinload(Run.stops).selectinload(Stop.consignment)
                 .selectinload(Consignment.packages),
                 selectinload(Run.vehicle), selectinload(Run.driver))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise NotFound(f"no run {run_id}")
    return run


# how a run that is past planning is described
GONE = {"departed": "has left", "done": "is finished", "cancelled": "was cancelled"}


def _planned(db: Session, run_id: int) -> Run:
    """The run, read after the lock, and still open to changes."""
    run = get_run(db, run_id)
    if run.status != "planned":
        raise Conflict("run_not_planned",
                       f"{run.number} {GONE.get(run.status, run.status)}; its stops can't change")
    return run


def run_load(run: Run) -> Load:
    return load(total(consignment_totals(s.consignment.packages) for s in run.stops),
                run.vehicle.max_kg, run.vehicle.max_m3)


def _vehicle(db: Session, rego: str) -> Vehicle:
    v = db.execute(select(Vehicle).where(
        Vehicle.rego == "".join(rego.split()).upper())).scalar_one_or_none()
    if v is None:
        raise FieldError("vehicle", f"no vehicle {rego}")
    if not v.active:
        raise FieldError("vehicle", f"{v.rego} is deactivated, so it can't take a run")
    return v


def _driver(db: Session, code: str) -> Driver:
    d = db.execute(select(Driver).where(Driver.code == code.lower())).scalar_one_or_none()
    if d is None:
        raise FieldError("driver", f"no driver {code}")
    if not d.active:
        raise FieldError("driver", f"{d.code} is deactivated, so they can't take a run")
    return d


def _truck_free(db: Session, vehicle: Vehicle, day: date, other_than: int | None = None) -> None:
    taken = db.execute(select(Run).where(
        Run.vehicle_id == vehicle.id, Run.day == day, Run.status != "cancelled",
        Run.id != (other_than or 0))).scalars().first()
    if taken is not None:
        raise FieldError("vehicle", f"{vehicle.rego} already has {taken.number} on {day}")


# a run its truck and driver are still needed for: not left yet, or on the road
LIVE = ("planned", "departed")


def refuse_if_on_a_run(db: Session, *, vehicle: Vehicle | None = None,
                       driver: Driver | None = None) -> None:
    """A truck or a driver with a run not finished isn't deactivated: a
    planned run could no longer leave, and a driver on the road would be
    signed out with deliveries still to send. Every such run is named, so
    the office knows what to change first. The caller holds the planning lock."""
    q = select(Run).where(Run.status.in_(LIVE))
    q = q.where(Run.vehicle_id == vehicle.id) if vehicle else q.where(Run.driver_id == driver.id)
    runs = db.execute(q.order_by(Run.day, Run.id)).scalars().all()
    if not runs:
        return
    who = vehicle.rego if vehicle else f"{driver.name} ({driver.code})"
    where = ", ".join(f"{r.number} (" + ("on the road now" if r.status == "departed"
                                         else f"planned for {fleet.long_date(r.day)}") + ")"
                      for r in runs)
    one = len(runs) == 1
    that = "that run" if one else "those runs"
    if any(r.status == "departed" for r in runs):
        then = (f"Deactivate {'it' if vehicle else 'them'} once {that} "
                f"{'is' if one else 'are'} finished.")
    else:
        then = (f"Give {that} another {'truck' if vehicle else 'driver'}, "
                f"or cancel {'it' if one else 'them'}, first.")
    raise Conflict("on_a_run", f"{who} is on {where}. {then}")


def create(db: Session, *, day: date, rego: str, driver_code: str, by: str) -> Run:
    lock_planning(db)
    vehicle, driver = _vehicle(db, rego), _driver(db, driver_code)
    fleet.refuse_if_blocked(db, vehicle, day)
    _truck_free(db, vehicle, day)
    run = Run(day=day, vehicle_id=vehicle.id, driver_id=driver.id, status="planned",
              created_by=by)
    db.add(run)
    db.flush()
    return run


def change(db: Session, run_id: int, *, rego: str | None, driver_code: str | None) -> Run:
    lock_planning(db)
    run = _planned(db, run_id)
    if rego is not None:
        vehicle = _vehicle(db, rego)
        fleet.refuse_if_blocked(db, vehicle, run.day)
        _truck_free(db, vehicle, run.day, other_than=run.id)
        run.vehicle_id = vehicle.id
    if driver_code is not None:
        run.driver_id = _driver(db, driver_code).id
    db.flush()
    return run


def cancel(db: Session, run_id: int) -> Run:
    """Only an empty planned run: take its stops off first, so each one's
    history says where it went."""
    lock_planning(db)
    run = get_run(db, run_id)
    if run.status == "cancelled":
        return run
    run = _planned(db, run_id)
    if run.stops:
        raise Conflict("run_not_empty",
                       f"{run.number} still has {len(run.stops)} stops; move them off first")
    run.status = "cancelled"
    db.flush()
    return run


# ---- stops -------------------------------------------------------------------

# a consignment that may still go on a run, or move to another; one the driver
# couldn't deliver comes back to the depot and is planned again, and one on
# the carrier bay can come off it onto a run
PLANNABLE = ("packed", "assigned", "failed", "booked")
# where one that can't be planned any more has gone, in words
_GONE = {"departed": "has left on its run", "delivered": "was delivered",
         "collected": "has been collected by a carrier"}


def gone(c: Consignment) -> str:
    """Why it can't move now: "Station Deli (0080034519) was delivered"."""
    return f"{c.customer} ({c.wms_ref}) {_GONE.get(c.status, f'is {c.status}')}"


def _now() -> datetime:
    return datetime.now(UTC)


def _live_stops(db: Session, run_id: int) -> list[Stop]:
    return list(db.execute(
        select(Stop).where(Stop.run_id == run_id, Stop.removed_at.is_(None))
        .order_by(Stop.sequence, Stop.id).execution_options(populate_existing=True)
    ).scalars())


def _renumber(stops: list[Stop]) -> None:
    for i, stop in enumerate(stops, start=1):
        stop.sequence = i


def _event(c: Consignment, kind: str, **detail) -> None:
    c.events.append(ConsignmentEvent(kind=kind, at=_now(), detail=detail))


def _plannable(db: Session, consignment_id: int) -> Consignment:
    c = db.execute(select(Consignment).where(Consignment.id == consignment_id)
                   .execution_options(populate_existing=True)).scalar_one_or_none()
    if c is None:
        raise FieldError("consignment_id", f"no consignment {consignment_id}")
    if c.status not in PLANNABLE:
        raise Conflict("not_plannable",
                       f"{gone(c)}, so it can't be planned")
    return c


def _current_stop(db: Session, consignment_id: int) -> Stop | None:
    """Its stop on a run now; a stop on a trip already made (it has an
    outcome) is a record."""
    return db.execute(select(Stop).where(
        Stop.consignment_id == consignment_id, Stop.removed_at.is_(None),
        Stop.outcome.is_(None))).scalar_one_or_none()


def refuse_if_not_back(db: Session, c: Consignment, then: str) -> None:
    """One that couldn't be delivered is on its truck until that run is
    finished: only then is it back at the depot, and Simple WMS to be told. It
    may be planned before that, but not leave."""
    out_on = db.execute(select(Run).join(Stop, Stop.run_id == Run.id).where(
        Stop.consignment_id == c.id, Stop.outcome == "failed", Run.status == "departed")
        .limit(1)).scalar_one_or_none()
    if out_on is not None:
        raise Conflict("not_back", f"{c.customer} ({c.wms_ref}) is still on {out_on.number}: "
                                   f"finish that run once the truck is back, then {then}")


def take_off_run(db: Session, c: Consignment, by: str) -> None:
    """Off the planned run it is on, if any, with a history row; the stops
    left are numbered again. The caller holds the planning lock and says
    where it goes next."""
    current = _current_stop(db, c.id)
    if current is None:
        return
    old = _planned(db, current.run_id)          # a departed run keeps its stops
    current.removed_at = _now()
    _event(c, "unassigned", run=old.number, run_id=old.id, by=by)
    db.flush()
    _renumber(_live_stops(db, old.id))


def assign(db: Session, run_id: int, consignment_id: int, position: int | None,
           by: str) -> Run:
    """Put a consignment on this run at a place in the order (1 is first; none
    is last), moving it off another planned run, or off the carrier bay, if
    it was on one. Asking for what is already so changes nothing."""
    from tms.services import carriers     # carriers plans with this module's lock

    lock_planning(db)
    run = _planned(db, run_id)
    if not run.vehicle.active:
        raise Conflict("vehicle_unavailable",
                       f"{run.vehicle.rego} is deactivated, so {run.number} can't take stops")
    fleet.refuse_if_blocked(db, run.vehicle, run.day)
    c = _plannable(db, consignment_id)
    carriers.take_off_bay(db, c, by)
    current = _current_stop(db, c.id)

    if current is not None and current.run_id != run.id:
        take_off_run(db, c, by)
        current = None

    stops = [s for s in _live_stops(db, run.id) if s.consignment_id != c.id]
    place = len(stops) + 1 if position is None else max(1, min(position, len(stops) + 1))
    if current is None:
        current = Stop(run_id=run.id, consignment_id=c.id, sequence=place, added_by=by)
        db.add(current)
        c.status = "assigned"
        _event(c, "assigned", run=run.number, run_id=run.id, by=by)
    stops.insert(place - 1, current)
    _renumber(stops)
    db.flush()
    return run


def unassign(db: Session, run_id: int, consignment_id: int, by: str) -> Run:
    """Back to the tray. Not on this run already: nothing to do."""
    lock_planning(db)
    run = _planned(db, run_id)
    stop = db.execute(select(Stop).where(
        Stop.run_id == run.id, Stop.consignment_id == consignment_id,
        Stop.removed_at.is_(None))).scalar_one_or_none()
    if stop is None:
        return run
    stop.removed_at = _now()
    c = db.get(Consignment, consignment_id)
    c.status = "packed"
    _event(c, "unassigned", run=run.number, run_id=run.id, by=by)
    db.flush()
    _renumber(_live_stops(db, run.id))
    db.flush()
    return run


def reorder(db: Session, run_id: int, consignment_ids: list[int], seen: list[int]) -> Run:
    """The whole order, as the dispatcher sees it, and the order they saw
    before they moved a stop. If the run has changed since (someone else
    added, took off or moved a stop), it is refused rather than guessed at or
    written over: the screen reloads and they drag again."""
    lock_planning(db)
    run = _planned(db, run_id)
    live = _live_stops(db, run.id)
    stops = {s.consignment_id: s for s in live}
    if (len(consignment_ids) != len(set(consignment_ids)) or set(consignment_ids) != set(stops)
            or seen != [s.consignment_id for s in live]):
        raise Conflict("stale", f"{run.number} changed while you were looking; it has been "
                                "reloaded, so try that again")
    _renumber([stops[cid] for cid in consignment_ids])
    db.flush()
    return run


# ---- leaving -----------------------------------------------------------------

# what Simple WMS records as the carrier when our own truck takes it
OWN_FLEET = "Own fleet"


def depart(db: Session, run_id: int, by: str, need_prestart: bool = False) -> Run:
    """The truck has left: each consignment is departed, and a ship call to
    Simple WMS waits in the queue for each, in the same transaction, so none
    is lost and none is made twice. A run that has already left is left as
    it is. The driver's Start (`need_prestart`) also wants the pre-start of
    the truck the run has, judged after the lock, like the truck itself."""
    lock_planning(db)
    run = get_run(db, run_id)
    if run.status == "departed":
        return run
    run = _planned(db, run_id)
    if not run.stops:
        raise Conflict("run_empty", f"{run.number} has no stops")
    if not run.vehicle.active:
        raise Conflict("vehicle_unavailable", f"{run.vehicle.rego} is deactivated")
    if not run.driver.active:
        raise Conflict("driver_unavailable", f"{run.driver.name} ({run.driver.code}) is deactivated")
    # a grounded or overdue truck says so, rather than asking for a check;
    # judged for the day it actually leaves, if that is after the run's own
    fleet.refuse_if_blocked(db, run.vehicle, max(run.day, fleet.today()))
    if need_prestart and fleet.run_prestart(db, run) is None:
        raise Conflict("prestart_needed",
                       f"Do the pre-start check of {run.vehicle.rego} before starting {run.number}.")
    over = run_load(run)
    if over.state == "over":
        raise Conflict("over_capacity", f"{run.number}: {over.message}")
    for stop in run.stops:
        refuse_if_not_back(db, stop.consignment, "this one can leave")
    now = _now()
    for stop in run.stops:
        c = stop.consignment
        c.status = "departed"
        _event(c, "departed", run=run.number, run_id=run.id, vehicle=run.vehicle.rego,
               driver=run.driver.code, by=by)
        message_id = uuid.uuid4()
        db.add(OutboundCall(
            message_id=message_id, kind="ship", run_id=run.id, consignment_id=c.id,
            path=f"/v1/deliveries/{quote(c.wms_ref, safe='')}/ship",
            body={"message_id": str(message_id), "owner": c.owner, "carrier": OWN_FLEET,
                  "tracking_no": run.number, "shipped_by": by[:64]},
            status="pending", attempts=0, next_attempt_at=now))
    run.status = "departed"
    run.departed_at = now
    db.flush()
    return run
