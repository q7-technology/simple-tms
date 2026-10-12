"""What is recorded about a truck: the records a fleet manager keeps (rego,
service, an odometer put right, and later fixed). Each is a new row, written
under the planning lock, so it and any run leaving take turns: a run never
leaves on a truck grounded or overdue a moment before."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.api.errors import NotFound
from tms.models import Vehicle, VehicleRecord
from tms.services.runs import lock_planning


def vehicle_by_rego(db: Session, rego: str) -> Vehicle:
    v = db.execute(select(Vehicle).where(
        Vehicle.rego == "".join(rego.split()).upper())).scalar_one_or_none()
    if v is None:
        raise NotFound(f"no vehicle {rego}")
    return v


def add(db: Session, rego: str, kind: str, *, by: str, note: str, **fields) -> VehicleRecord:
    lock_planning(db)
    v = vehicle_by_rego(db, rego)
    row = VehicleRecord(vehicle_id=v.id, kind=kind, note=note, recorded_by=by,
                        recorded_at=datetime.now(UTC), **fields)
    db.add(row)
    db.flush()
    return row
