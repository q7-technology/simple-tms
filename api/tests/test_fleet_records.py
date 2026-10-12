"""Pre-start checks and what a fleet manager records are kept as they were
sent: never changed, never deleted (rule 1)."""
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from runs_samples import DAY, driver, vehicle
from tms.models import ChecklistItem, PrestartCheck, PrestartItem, Run, VehicleRecord


def test_a_fresh_install_has_the_designs_checklist_for_each_type(db):
    rows = db.execute(select(ChecklistItem).where(ChecklistItem.retired_at.is_(None))
                      .order_by(ChecklistItem.id)).scalars().all()
    assert {r.vehicle_type for r in rows} == {"van", "rigid", "ute"}
    rigid = [(r.label, r.critical) for r in rows if r.vehicle_type == "rigid"]
    assert rigid == [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
                     ("Load restraints", False), ("Damage", False)]


def _a_check(db) -> PrestartCheck:
    v, d = vehicle(db), driver(db)
    run = Run(day=DAY, vehicle_id=v.id, driver_id=d.id, status="planned", created_by="dee")
    db.add(run)
    db.flush()
    now = datetime.now(UTC)
    check = PrestartCheck(message_id=uuid.uuid4(), run_id=run.id, vehicle_id=v.id, driver_id=d.id,
                          at=now, odometer_km=61208, result="passed", recorded_by="kim",
                          recorded_at=now)
    check.items.append(PrestartItem(position=1, checklist_item_id=1, label="Tyres",
                                    critical=False, passed=True))
    db.add(check)
    db.add(VehicleRecord(vehicle_id=v.id, kind="rego", expires_on=date(2027, 3, 3),
                         note="Renewed online", recorded_by="fleet.mgr", recorded_at=now))
    db.commit()
    return check


@pytest.mark.parametrize("table", ["prestart_check", "prestart_item", "vehicle_record"])
def test_a_check_or_a_record_cannot_be_changed_or_deleted(db, table):
    _a_check(db)
    column = {"prestart_check": "odometer_km = 1", "prestart_item": "passed = false",
              "vehicle_record": "note = 'changed'"}[table]
    for sql in (f"update {table} set {column}", f"delete from {table}"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(sql))
        db.rollback()
