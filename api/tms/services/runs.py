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

from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import Consignment, Driver, Run, Stop, Vehicle
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
