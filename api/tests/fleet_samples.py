"""Checks and records made straight in the database, so a test of where a
truck stands starts from its history."""
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select

from runs_samples import DAY
from tms.models import Driver, PrestartCheck, PrestartItem, Run, Vehicle, VehicleRecord


def _run_for(db, v: Vehicle) -> Run:
    run = db.execute(select(Run).where(Run.vehicle_id == v.id)).scalars().first()
    if run is None:
        d = db.execute(select(Driver)).scalars().first()
        if d is None:
            d = Driver(code="tom", name="Tom", pin_hash="x", failed_attempts=0, locked=False,
                       active=True)
            db.add(d)
            db.flush()
        run = Run(day=DAY, vehicle_id=v.id, driver_id=d.id, status="done", created_by="dee")
        db.add(run)
        db.flush()
    return run


def check(db, v: Vehicle, km: int, failed: tuple[str, ...] = (), by="tom") -> PrestartCheck:
    """A pre-start of the design's six items, with these failed (Lights and
    Brakes are critical)."""
    labels = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
              ("Load restraints", False), ("Damage", False)]
    critical = any(c for label, c in labels if label in failed)
    now = datetime.now(UTC)
    c = PrestartCheck(message_id=uuid.uuid4(), run_id=_run_for(db, v).id, vehicle_id=v.id,
                      at=now, odometer_km=km, recorded_by=by, recorded_at=now,
                      result="grounded" if critical else "failed" if failed else "passed")
    for i, (label, crit) in enumerate(labels, start=1):
        c.items.append(PrestartItem(position=i, checklist_item_id=i, label=label, critical=crit,
                                    passed=label not in failed,
                                    note="Not right" if label in failed else None))
    db.add(c)
    db.commit()
    return c


def record(db, v: Vehicle, kind: str, note="Done", **fields) -> VehicleRecord:
    r = VehicleRecord(vehicle_id=v.id, kind=kind, note=note, recorded_by="fleet.mgr",
                      recorded_at=datetime.now(UTC), **fields)
    db.add(r)
    db.commit()
    return r


def serviced(db, v: Vehicle, on: date, km: int | None = None) -> VehicleRecord:
    return record(db, v, "service", done_on=on, odometer_km=km)
