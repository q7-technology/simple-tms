"""Vehicles: created, changed, deactivated. Never deleted."""
from __future__ import annotations

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound, violated_constraint
from tms.api.schemas import Dec, Page
from tms.models import Vehicle
from tms.services import fleet

router = APIRouter(tags=["vehicles"])

VehicleType = Literal["van", "rigid", "ute"]


def normal_rego(value: str) -> str:
    """'abc 123' and 'ABC123' are the same plate."""
    return "".join(value.split()).upper()


class VehicleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rego: str = Field(min_length=1, max_length=12, pattern=r"^[A-Z0-9-]+$")
    type: VehicleType
    max_kg: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    max_m3: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    # serviced every so many months and/or kilometres, whichever comes first
    service_every_months: int | None = Field(default=None, ge=1, le=120)
    service_every_km: int | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("rego", mode="before")
    @classmethod
    def _rego(cls, v):
        return normal_rego(v) if isinstance(v, str) else v


# PATCH fields that an explicit null clears
CLEARABLE = {"service_every_months", "service_every_km"}


class VehiclePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: VehicleType | None = None
    max_kg: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    max_m3: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    service_every_months: int | None = Field(default=None, ge=1, le=120)
    service_every_km: int | None = Field(default=None, ge=1, le=1_000_000)


class RegoDue(BaseModel):
    expires_on: dt.date | None
    days_left: int | None
    # ok, due (30 days or less), overdue, unknown (no expiry recorded)
    state: str


class ServiceDue(BaseModel):
    last_on: dt.date | None
    last_km: int | None
    due_on: dt.date | None
    due_km: int | None
    days_left: int | None
    km_left: int | None
    # ok, due (30 days or 1,000 km or less), overdue, unknown
    state: str


class Grounded(BaseModel):
    check_id: int
    at: datetime
    failed: list[str]


class CheckBrief(BaseModel):
    id: int
    at: datetime
    result: str
    by: str


class Blocked(BaseModel):
    code: str
    message: str


class VehicleOut(BaseModel):
    id: int
    rego: str
    type: str
    max_kg: Dec
    max_m3: Dec
    active: bool
    created_at: datetime
    service_every_months: int | None
    service_every_km: int | None
    odometer_km: int | None
    rego_due: RegoDue
    service_due: ServiceDue
    # the check that grounded it, until a fleet manager marks it fixed
    grounded: Grounded | None
    last_prestart: CheckBrief | None
    # grounded, overdue, due or ok, on the day asked about
    state: str
    # why it can't take a run that day, or null
    blocked: Blocked | None


def blocked_out(s: fleet.Standing) -> Blocked | None:
    why = s.blocked
    return None if why is None else Blocked(code=why.code, message=why.message)


def vehicle_out(db, v: Vehicle, day: dt.date | None = None) -> VehicleOut:
    s = fleet.standing(db, v, day or fleet.today())
    g, last = s.grounded_by, s.last_check
    return VehicleOut(
        id=v.id, rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
        active=v.active, created_at=v.created_at, service_every_months=v.service_every_months,
        service_every_km=v.service_every_km, odometer_km=s.odometer_km,
        rego_due=RegoDue(expires_on=s.rego_expires_on, days_left=s.rego_days_left,
                         state=s.rego_state),
        service_due=ServiceDue(
            last_on=s.last_service.done_on if s.last_service else None,
            last_km=s.last_service.odometer_km if s.last_service else None,
            due_on=s.service_due_on, due_km=s.service_due_km, days_left=s.service_days_left,
            km_left=s.service_km_left, state=s.service_state),
        grounded=None if g is None else Grounded(check_id=g.id, at=g.at,
                                                 failed=fleet.failed_critical(g)),
        last_prestart=None if last is None else CheckBrief(id=last.id, at=last.at,
                                                           result=last.result,
                                                           by=last.recorded_by),
        state=s.state, blocked=blocked_out(s))


def _get(db, rego: str) -> Vehicle:
    v = db.execute(select(Vehicle).where(Vehicle.rego == normal_rego(rego))).scalar_one_or_none()
    if v is None:
        raise NotFound(f"no vehicle {rego}")
    return v


@router.post("/vehicles", status_code=201, response_model=VehicleOut)
def create_vehicle(body: VehicleIn, db: DB, who: Principal = require("fleet:write")):
    if db.execute(select(Vehicle).where(Vehicle.rego == body.rego)).scalar_one_or_none():
        raise FieldError("rego", f"vehicle {body.rego} already exists")
    v = Vehicle(rego=body.rego, type=body.type, max_kg=body.max_kg, max_m3=body.max_m3,
                service_every_months=body.service_every_months,
                service_every_km=body.service_every_km, active=True)
    db.add(v)
    try:
        db.commit()
    except IntegrityError as e:
        # another request took the rego between the check above and this insert
        db.rollback()
        if violated_constraint(e) == "uq_vehicle_rego":
            raise FieldError("rego", f"vehicle {body.rego} already exists") from None
        raise
    return JSONResponse(status_code=201, content=vehicle_out(db, v).model_dump(mode="json"))


@router.get("/vehicles", response_model=Page[VehicleOut])
def list_vehicles(db: DB, active: bool | None = None,
                  day: dt.date | None = Query(default=None, alias="date"),
                  who: Principal = require("fleet:read")):
    """`date` is the day due and overdue are judged for: the caller's today.
    Left out, today in UTC."""
    q = select(Vehicle)
    if active is not None:
        q = q.where(Vehicle.active.is_(active))
    rows = db.execute(q.order_by(Vehicle.rego)).scalars().all()
    return Page(items=[vehicle_out(db, v, day) for v in rows], total=len(rows))


@router.get("/vehicles/{rego}", response_model=VehicleOut)
def get_vehicle(rego: str, db: DB, day: dt.date | None = Query(default=None, alias="date"),
                who: Principal = require("fleet:read")):
    return vehicle_out(db, _get(db, rego), day)


@router.patch("/vehicles/{rego}", response_model=VehicleOut)
def update_vehicle(rego: str, body: VehiclePatch, db: DB, who: Principal = require("fleet:write")):
    """Only what is given changes. A service interval given as null is
    cleared (never due that way); a field left out is kept."""
    v = _get(db, rego)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is not None or field in CLEARABLE:
            setattr(v, field, value)
    db.commit()
    return vehicle_out(db, v)


@router.post("/vehicles/{rego}/deactivate", response_model=VehicleOut)
def deactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
    """Off the road for good, or sold. Its history stays."""
    v = _get(db, rego)
    v.active = False
    db.commit()
    return vehicle_out(db, v)


@router.post("/vehicles/{rego}/reactivate", response_model=VehicleOut)
def reactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
    v = _get(db, rego)
    v.active = True
    db.commit()
    return vehicle_out(db, v)
