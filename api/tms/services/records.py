"""What is recorded about a truck: a driver's pre-start check, and the
records a fleet manager keeps (rego, service, an odometer put right, fixed).
Each is a new row, written under the planning lock, so it and any run leaving
take turns: a run never leaves on a truck grounded or overdue a moment
before."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import PrestartCheck, PrestartItem, Run, Vehicle, VehicleRecord
from tms.services import checklists, deliveries, fleet
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


def run_check(db: Session, run: Run) -> PrestartCheck | None:
    """The latest pre-start for this run, of the truck it has now."""
    return db.execute(select(PrestartCheck).where(
        PrestartCheck.run_id == run.id, PrestartCheck.vehicle_id == run.vehicle_id)
        .order_by(PrestartCheck.id.desc()).limit(1)).scalar_one_or_none()


def prestart(db: Session, who, run_id: int, *, message_id: uuid.UUID, at: datetime,
             odometer_km: int, answers: list[dict]) -> PrestartCheck:
    """The driver's check of the run's truck, every item of its checklist
    answered once. A failed critical item grounds the truck there and then; a
    failed other item is kept and the truck may go. Kept as sent, for good."""
    lock_planning(db)
    run = deliveries.own_run(db, who, run_id)
    if run.status == "done":
        raise Conflict("run_done", f"{run.number} is finished")
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")
    v = run.vehicle
    lines = checklists.live(db, v.type)
    given = {a["item_id"]: a for a in answers}
    if len(given) != len(answers) or set(given) != {line.id for line in lines}:
        raise Conflict("checklist_changed",
                       f"The {v.type} checklist has changed since this check began. "
                       "Do the check again.")
    reading = fleet.odometer(db, v.id)
    if reading is not None and odometer_km < reading:
        raise FieldError("odometer_km",
                         f"{odometer_km:,} km is less than {v.rego}'s last reading, "
                         f"{reading:,} km. Check the odometer and try again.")
    photos = [a["photo_file"] for a in answers if a["photo_file"]]
    if len(photos) != len(set(photos)):
        raise FieldError("photo_file", "each failed item needs a photo of its own")
    for photo in photos:
        deliveries.own_file(db, who, photo, "photo")
    failed = [line for line in lines if given[line.id]["result"] == "fail"]
    now = datetime.now(UTC)
    check = PrestartCheck(
        message_id=message_id, run_id=run.id, vehicle_id=v.id,
        driver_id=who.id if who.kind == "driver" else None, at=min(at, now),
        odometer_km=odometer_km, recorded_by=who.name, recorded_at=now,
        result=("grounded" if any(line.critical for line in failed)
                else "failed" if failed else "passed"))
    for position, line in enumerate(lines, start=1):
        a = given[line.id]
        check.items.append(PrestartItem(
            position=position, checklist_item_id=line.id, label=line.label,
            critical=line.critical, passed=a["result"] == "pass", note=a["note"],
            photo_file_id=a["photo_file"]))
    db.add(check)
    db.flush()
    return check
