"""Vehicles: created, changed, deactivated. Never deleted."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound, violated_constraint
from tms.api.schemas import Dec, Page
from tms.models import Vehicle

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

    @field_validator("rego", mode="before")
    @classmethod
    def _rego(cls, v):
        return normal_rego(v) if isinstance(v, str) else v


class VehiclePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: VehicleType | None = None
    max_kg: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    max_m3: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)


class VehicleOut(BaseModel):
    id: int
    rego: str
    type: str
    max_kg: Dec
    max_m3: Dec
    active: bool
    created_at: datetime


def vehicle_out(v: Vehicle) -> VehicleOut:
    return VehicleOut(id=v.id, rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
                      active=v.active, created_at=v.created_at)


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
                active=True)
    db.add(v)
    try:
        db.commit()
    except IntegrityError as e:
        # another request took the rego between the check above and this insert
        db.rollback()
        if violated_constraint(e) == "uq_vehicle_rego":
            raise FieldError("rego", f"vehicle {body.rego} already exists") from None
        raise
    return JSONResponse(status_code=201, content=vehicle_out(v).model_dump(mode="json"))


@router.get("/vehicles", response_model=Page[VehicleOut])
def list_vehicles(db: DB, active: bool | None = None, who: Principal = require("fleet:read")):
    q = select(Vehicle)
    if active is not None:
        q = q.where(Vehicle.active.is_(active))
    rows = db.execute(q.order_by(Vehicle.rego)).scalars().all()
    return Page(items=[vehicle_out(v) for v in rows], total=len(rows))


@router.get("/vehicles/{rego}", response_model=VehicleOut)
def get_vehicle(rego: str, db: DB, who: Principal = require("fleet:read")):
    return vehicle_out(_get(db, rego))


@router.patch("/vehicles/{rego}", response_model=VehicleOut)
def update_vehicle(rego: str, body: VehiclePatch, db: DB, who: Principal = require("fleet:write")):
    v = _get(db, rego)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(v, field, value)
    db.commit()
    return vehicle_out(v)


@router.post("/vehicles/{rego}/deactivate", response_model=VehicleOut)
def deactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
    """Off the road for good, or sold. Its history stays."""
    v = _get(db, rego)
    v.active = False
    db.commit()
    return vehicle_out(v)


@router.post("/vehicles/{rego}/reactivate", response_model=VehicleOut)
def reactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
    v = _get(db, rego)
    v.active = True
    db.commit()
    return vehicle_out(v)
