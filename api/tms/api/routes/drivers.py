"""Drivers: created, changed, deactivated. Never deleted."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound
from tms.api.schemas import Page
from tms.models import Driver
from tms.services.access import hash_password

router = APIRouter(tags=["drivers"])

PIN = r"^[0-9]{4,8}$"


class DriverIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=120)
    pin: str = Field(pattern=PIN)


class DriverPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    pin: str | None = Field(default=None, pattern=PIN)


class DriverOut(BaseModel):
    id: int
    code: str
    name: str
    active: bool
    locked: bool
    failed_attempts: int
    created_at: datetime


def driver_out(d: Driver) -> DriverOut:
    return DriverOut(id=d.id, code=d.code, name=d.name, active=d.active, locked=d.locked,
                     failed_attempts=d.failed_attempts, created_at=d.created_at)


def _get(db, code: str) -> Driver:
    d = db.execute(select(Driver).where(Driver.code == code)).scalar_one_or_none()
    if d is None:
        raise NotFound(f"no driver {code}")
    return d


@router.post("/drivers", status_code=201, response_model=DriverOut)
def create_driver(body: DriverIn, db: DB, who: Principal = require("fleet:write")):
    if db.execute(select(Driver).where(Driver.code == body.code)).scalar_one_or_none():
        raise FieldError("code", f"driver {body.code} already exists")
    d = Driver(code=body.code, name=body.name, pin_hash=hash_password(body.pin),
               failed_attempts=0, locked=False, active=True)
    db.add(d)
    db.commit()
    return JSONResponse(status_code=201, content=driver_out(d).model_dump(mode="json"))


@router.get("/drivers", response_model=Page[DriverOut])
def list_drivers(db: DB, active: bool | None = None, who: Principal = require("fleet:read")):
    q = select(Driver)
    if active is not None:
        q = q.where(Driver.active.is_(active))
    rows = db.execute(q.order_by(Driver.code)).scalars().all()
    return Page(items=[driver_out(d) for d in rows], total=len(rows))


@router.get("/drivers/{code}", response_model=DriverOut)
def get_driver(code: str, db: DB, who: Principal = require("fleet:read")):
    return driver_out(_get(db, code))


@router.patch("/drivers/{code}", response_model=DriverOut)
def update_driver(code: str, body: DriverPatch, db: DB, who: Principal = require("fleet:write")):
    d = _get(db, code)
    if body.name is not None:
        d.name = body.name
    if body.pin is not None:
        d.pin_hash = hash_password(body.pin)
    db.commit()
    return driver_out(d)


@router.post("/drivers/{code}/deactivate", response_model=DriverOut)
def deactivate_driver(code: str, db: DB, who: Principal = require("fleet:write")):
    """Deactivated, never deleted: their runs and deliveries stay theirs."""
    d = _get(db, code)
    d.active = False
    db.commit()
    return driver_out(d)


@router.post("/drivers/{code}/reactivate", response_model=DriverOut)
def reactivate_driver(code: str, db: DB, who: Principal = require("fleet:write")):
    d = _get(db, code)
    d.active = True
    db.commit()
    return driver_out(d)


@router.post("/drivers/{code}/unlock", response_model=DriverOut)
def unlock_driver(code: str, db: DB, who: Principal = require("fleet:write")):
    d = _get(db, code)
    d.locked = False
    d.failed_attempts = 0
    db.commit()
    return driver_out(d)
