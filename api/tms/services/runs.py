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

from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import Consignment, ConsignmentEvent, Driver, Run, Stop, Vehicle
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


def _planned(db: Session, run_id: int) -> Run:
    """The run, read after the lock, and still open to changes."""
    run = get_run(db, run_id)
    if run.status != "planned":
        raise Conflict("run_not_planned",
                       f"{run.number} has {run.status}; its stops can't change")
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
    d = db.execute(select(Driver).where(Driver.code == code)).scalar_one_or_none()
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


def create(db: Session, *, day: date, rego: str, driver_code: str, by: str) -> Run:
    lock_planning(db)
    vehicle, driver = _vehicle(db, rego), _driver(db, driver_code)
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

# a consignment that may still go on a run, or move to another
PLANNABLE = ("packed", "assigned")


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
                       f"{c.customer} ({c.wms_ref}) has {c.status}, so it can't be planned")
    return c


def assign(db: Session, run_id: int, consignment_id: int, position: int | None,
           by: str) -> Run:
    """Put a consignment on this run at a place in the order (1 is first; none
    is last), moving it off another planned run if it was on one. Asking for
    what is already so changes nothing."""
    lock_planning(db)
    run = _planned(db, run_id)
    if not run.vehicle.active:
        raise Conflict("vehicle_unavailable",
                       f"{run.vehicle.rego} is deactivated, so {run.number} can't take stops")
    c = _plannable(db, consignment_id)
    current = db.execute(select(Stop).where(
        Stop.consignment_id == c.id, Stop.removed_at.is_(None))).scalar_one_or_none()

    if current is not None and current.run_id != run.id:
        old = _planned(db, current.run_id)          # a departed run keeps its stops
        current.removed_at = _now()
        _event(c, "unassigned", run=old.number, run_id=old.id, by=by)
        db.flush()
        _renumber(_live_stops(db, old.id))
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


def reorder(db: Session, run_id: int, consignment_ids: list[int]) -> Run:
    """The whole order, as the dispatcher sees it. If the run has changed
    since (someone else added or took off a stop), it is refused rather than
    guessed at: the screen reloads and they drag again."""
    lock_planning(db)
    run = _planned(db, run_id)
    stops = {s.consignment_id: s for s in _live_stops(db, run.id)}
    if len(consignment_ids) != len(set(consignment_ids)) or set(consignment_ids) != set(stops):
        raise Conflict("stale", f"{run.number} changed while you were looking; it has been "
                                "reloaded, so try that again")
    _renumber([stops[cid] for cid in consignment_ids])
    db.flush()
    return run
