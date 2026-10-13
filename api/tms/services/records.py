"""What is recorded about a truck: a driver's pre-start check, and the
records a fleet manager keeps (rego, service, an odometer put right, fixed).
Each is a new row, written under the planning lock, so it and any run leaving
take turns: a run never leaves on a truck grounded or overdue a moment
before."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
    """The run's pre-start of the truck it has now, as Start counts it."""
    return fleet.run_prestart(db, run)


def prestart(db: Session, who, run_id: int, *, rego: str, message_id: uuid.UUID, at: datetime,
             odometer_km: int, answers: list[dict]) -> PrestartCheck:
    """The driver's check of the truck they were shown (`rego`), every item of
    its checklist answered once. A failed critical item grounds that truck
    there and then; a failed other item is kept and the truck may go. Kept as
    sent, for good. If the office has given the run another truck since, the
    check is still kept against the truck inspected (what failed grounds the
    truck that failed); the caller says the run needs a fresh check."""
    lock_planning(db)
    run = deliveries.own_run(db, who, run_id)
    if run.status == "done":
        raise Conflict("run_done", f"{run.number} is finished")
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")
    if run.status == "planned" and run.day > fleet.today() + timedelta(days=1):
        # a day's grace: the server's clock is UTC, and an Australian morning is
        # still yesterday there
        raise Conflict("too_early", f"{run.number} is for {fleet.long_date(run.day)}. "
                                    "Do the pre-start on the day.")
    v = db.execute(select(Vehicle).where(
        Vehicle.rego == "".join(rego.split()).upper())).scalar_one_or_none()
    if v is None:
        raise FieldError("vehicle", f"no vehicle {rego}")
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
    # a photo's problem is named by the item it is on, as sent: items.4.photo_file
    seen = set()
    for n, a in enumerate(answers):
        if not a["photo_file"]:
            continue
        field = f"items.{n}.photo_file"
        if a["photo_file"] in seen:
            raise FieldError(field, "each failed item needs a photo of its own")
        seen.add(a["photo_file"])
        try:
            deliveries.own_file(db, who, a["photo_file"], "photo")
        except FieldError as e:
            raise FieldError(field, e.message) from None
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


def fix(db: Session, rego: str, *, check_id: int, by: str, note: str) -> VehicleRecord:
    """A fleet manager has put right what grounded the truck. The record names
    the check they looked at, which must be the latest that grounded it: if
    another came in meanwhile, it is refused rather than cleared unseen."""
    lock_planning(db)
    v = vehicle_by_rego(db, rego)
    grounded_by = fleet.grounding(db, v.id)
    if grounded_by is None:
        raise Conflict("not_grounded", f"{v.rego} isn't grounded")
    if grounded_by.id != check_id:
        raise Conflict("stale", f"Another failed check of {v.rego} has come in; it has been "
                                "reloaded, so look at it and mark it fixed again")
    row = VehicleRecord(vehicle_id=v.id, kind="fixed", check_id=check_id, note=note,
                        recorded_by=by, recorded_at=datetime.now(UTC))
    db.add(row)
    db.flush()
    return row


def fixed_by(db: Session, check: PrestartCheck) -> VehicleRecord | None:
    """For a check that grounded its truck: the record that cleared it, if any."""
    if check.result != "grounded":
        return None
    return db.execute(select(VehicleRecord).where(
        VehicleRecord.vehicle_id == check.vehicle_id, VehicleRecord.kind == "fixed",
        VehicleRecord.check_id >= check.id).order_by(VehicleRecord.id).limit(1)
    ).scalar_one_or_none()
