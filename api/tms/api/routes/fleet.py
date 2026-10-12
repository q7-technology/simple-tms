"""Fleet care: the pre-start checklists, the checks drivers send, and what a
fleet manager records about each truck. Checks and records are kept as they
were sent; a truck's standing is worked out from them."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import NotFound
from tms.api.routes.vehicles import VehicleType
from tms.models import ChecklistItem, Driver, PrestartCheck, Run, Vehicle, VehicleRecord
from tms.services import checklists, fleet, records
from tms.services.envelope import handle

router = APIRouter(tags=["fleet"])


# ---- checklists ----------------------------------------------------------------

class ChecklistLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    critical: bool = False


class ChecklistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ChecklistLine] = Field(min_length=1, max_length=30)

    @field_validator("items")
    @classmethod
    def _once_each(cls, items: list[ChecklistLine]) -> list[ChecklistLine]:
        labels = [i.label.casefold() for i in items]
        if len(labels) != len(set(labels)):
            raise ValueError("each item once: two have the same label")
        return items


class ChecklistItemOut(BaseModel):
    id: int
    label: str
    critical: bool


def item_out(i: ChecklistItem) -> ChecklistItemOut:
    return ChecklistItemOut(id=i.id, label=i.label, critical=i.critical)


@router.get("/checklists", response_model=dict[str, list[ChecklistItemOut]])
def get_checklists(db: DB, who: Principal = require("fleet:read")):
    """Each type's checklist, in order: { "van": [...], "rigid": [...], "ute": [...] }."""
    return {t: [item_out(i) for i in checklists.live(db, t)] for t in checklists.TYPES}


@router.put("/checklists/{vehicle_type}", response_model=list[ChecklistItemOut])
def put_checklist(vehicle_type: VehicleType, body: ChecklistIn, db: DB,
                  who: Principal = require("fleet:write")):
    """The whole list for one type, in order. Lines that go are retired."""
    rows = checklists.replace(db, vehicle_type, [(i.label, i.critical) for i in body.items])
    db.commit()
    return [item_out(i) for i in rows]


# ---- the driver's pre-start ---------------------------------------------------

class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    item_id: int
    result: Literal["pass", "fail"]
    note: str | None = Field(default=None, max_length=500)
    photo_file: uuid.UUID | None = None

    @model_validator(mode="after")
    def _a_fail_says_why(self):
        if self.result == "fail" and (not self.note or not self.photo_file):
            raise ValueError("a failed item needs a photo and a note")
        if self.result == "pass" and (self.note or self.photo_file):
            raise ValueError("only a failed item takes a note and a photo")
        return self


class PrestartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: uuid.UUID
    at: AwareDatetime
    odometer_km: int = Field(ge=0, le=9_999_999)
    items: list[Answer] = Field(min_length=1, max_length=30)


class PrestartOut(BaseModel):
    message_id: uuid.UUID
    check_id: int
    run_id: int
    run_no: str
    vehicle: str
    # passed, failed (a non-critical item: the truck may go), grounded
    result: str
    failed: list[str]
    # whether the truck is grounded now, by this check or one before it
    grounded: bool
    odometer_km: int
    at: dt.datetime


@router.post("/runs/{id}/prestart", response_model=PrestartOut)
def send_prestart(id: int, body: PrestartIn, db: DB,
                  who: Principal = require("deliveries:write")):
    """The driver's pre-start check of the run's truck, once per message_id."""
    def work():
        check = records.prestart(db, who, id, message_id=body.message_id, at=body.at,
                                 odometer_km=body.odometer_km,
                                 answers=[a.model_dump() for a in body.items])
        run = db.get(Run, check.run_id)
        return PrestartOut(message_id=body.message_id, check_id=check.id, run_id=run.id,
                           run_no=run.number, vehicle=run.vehicle.rego, result=check.result,
                           failed=[i.label for i in check.items if not i.passed],
                           grounded=fleet.grounding(db, check.vehicle_id) is not None,
                           odometer_km=check.odometer_km, at=check.at)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/prestart", work)


# ---- what a fleet manager records -----------------------------------------------

class RegoIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expires_on: dt.date
    note: str = Field(min_length=1, max_length=500)


class ServiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    done_on: dt.date
    odometer_km: int | None = Field(default=None, ge=0, le=9_999_999)
    note: str = Field(min_length=1, max_length=500)


class OdometerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    odometer_km: int = Field(ge=0, le=9_999_999)
    note: str = Field(min_length=1, max_length=500)


class RecordOut(BaseModel):
    id: int
    vehicle: str
    kind: str
    check_id: int | None
    done_on: dt.date | None
    expires_on: dt.date | None
    odometer_km: int | None
    note: str
    recorded_by: str
    recorded_at: dt.datetime


def record_out(r: VehicleRecord, rego: str) -> RecordOut:
    return RecordOut(id=r.id, vehicle=rego, kind=r.kind, check_id=r.check_id,
                     done_on=r.done_on, expires_on=r.expires_on, odometer_km=r.odometer_km,
                     note=r.note, recorded_by=r.recorded_by, recorded_at=r.recorded_at)


def _created(db, row: VehicleRecord, rego: str) -> JSONResponse:
    db.commit()
    out = record_out(row, records.vehicle_by_rego(db, rego).rego)
    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))


@router.post("/vehicles/{rego}/rego", status_code=201, response_model=RecordOut)
def record_rego(rego: str, body: RegoIn, db: DB, who: Principal = require("fleet:write")):
    """The rego's new expiry. The newest record is the one that counts."""
    row = records.add(db, rego, "rego", by=who.name, note=body.note, expires_on=body.expires_on)
    return _created(db, row, rego)


@router.post("/vehicles/{rego}/service", status_code=201, response_model=RecordOut)
def record_service(rego: str, body: ServiceIn, db: DB, who: Principal = require("fleet:write")):
    """A service done: the next one falls due from it, by date and by km."""
    row = records.add(db, rego, "service", by=who.name, note=body.note, done_on=body.done_on,
                      odometer_km=body.odometer_km)
    return _created(db, row, rego)


@router.post("/vehicles/{rego}/odometer", status_code=201, response_model=RecordOut)
def record_odometer(rego: str, body: OdometerIn, db: DB, who: Principal = require("fleet:write")):
    """The reading put right (a slip of the finger, a new odometer): readings
    from before it no longer count."""
    row = records.add(db, rego, "odometer", by=who.name, note=body.note,
                      odometer_km=body.odometer_km)
    return _created(db, row, rego)


class FixIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # the check that grounded it, as the fleet manager saw it
    check_id: int
    note: str = Field(min_length=1, max_length=500)


@router.post("/vehicles/{rego}/fix", status_code=201, response_model=RecordOut)
def mark_fixed(rego: str, body: FixIn, db: DB, who: Principal = require("fleet:write")):
    """What grounded it is put right: a new record with what was done. The
    failed check is never changed."""
    row = records.fix(db, rego, check_id=body.check_id, by=who.name, note=body.note)
    return _created(db, row, rego)


class PrestartItemOut(BaseModel):
    label: str
    critical: bool
    passed: bool
    note: str | None
    photo_file: uuid.UUID | None


class FixedOut(BaseModel):
    id: int
    note: str
    by: str
    at: dt.datetime


class PrestartDetail(BaseModel):
    id: int
    run_id: int
    run_no: str
    vehicle: str
    vehicle_type: str
    driver: dict | None
    at: dt.datetime
    recorded_at: dt.datetime
    odometer_km: int
    result: str
    items: list[PrestartItemOut]
    # for a check that grounded the truck: the record that cleared it, or null
    fixed: FixedOut | None


@router.get("/prestarts/{check_id}", response_model=PrestartDetail)
def get_prestart(check_id: int, db: DB, who: Principal = require("fleet:read")):
    """One check as it was sent, with each item, note and photo (the photos
    are at GET /v1/pod-files/{id})."""
    check = db.get(PrestartCheck, check_id)
    if check is None:
        raise NotFound(f"no pre-start check {check_id}")
    run, v = db.get(Run, check.run_id), db.get(Vehicle, check.vehicle_id)
    d = db.get(Driver, check.driver_id) if check.driver_id else None
    fix = records.fixed_by(db, check)
    return PrestartDetail(
        id=check.id, run_id=run.id, run_no=run.number, vehicle=v.rego, vehicle_type=v.type,
        driver=None if d is None else {"code": d.code, "name": d.name}, at=check.at,
        recorded_at=check.recorded_at, odometer_km=check.odometer_km, result=check.result,
        items=[PrestartItemOut(label=i.label, critical=i.critical, passed=i.passed, note=i.note,
                               photo_file=i.photo_file_id) for i in check.items],
        fixed=None if fix is None else FixedOut(id=fix.id, note=fix.note, by=fix.recorded_by,
                                                at=fix.recorded_at))


class HistoryItem(BaseModel):
    # prestart, fixed, service, rego, odometer
    kind: str
    id: int
    recorded_at: dt.datetime
    by: str
    note: str | None = None
    # a pre-start: passed, failed or grounded, and each item that failed
    result: str | None = None
    failed: list[str] = []
    odometer_km: int | None = None
    done_on: dt.date | None = None
    expires_on: dt.date | None = None
    check_id: int | None = None


@router.get("/vehicles/{rego}/history", response_model=list[HistoryItem])
def vehicle_history(rego: str, db: DB, limit: int = Query(default=50, ge=1, le=200),
                    who: Principal = require("fleet:read")):
    """Its checks and records, newest first. Nothing in it is ever changed."""
    v = records.vehicle_by_rego(db, rego)
    checks = db.execute(select(PrestartCheck).where(PrestartCheck.vehicle_id == v.id)
                        .order_by(PrestartCheck.id.desc()).limit(limit)).scalars().all()
    rows = db.execute(select(VehicleRecord).where(VehicleRecord.vehicle_id == v.id)
                      .order_by(VehicleRecord.id.desc()).limit(limit)).scalars().all()
    items = [HistoryItem(kind="prestart", id=c.id, recorded_at=c.recorded_at, by=c.recorded_by,
                         result=c.result, failed=[i.label for i in c.items if not i.passed],
                         odometer_km=c.odometer_km) for c in checks]
    items += [HistoryItem(kind=r.kind, id=r.id, recorded_at=r.recorded_at, by=r.recorded_by,
                          note=r.note, odometer_km=r.odometer_km, done_on=r.done_on,
                          expires_on=r.expires_on, check_id=r.check_id) for r in rows]
    return sorted(items, key=lambda i: i.recorded_at, reverse=True)[:limit]
