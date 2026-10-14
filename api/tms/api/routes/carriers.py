"""Carriers: who else takes freight. Created, changed, deactivated by a
fleet manager; never deleted."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound, violated_constraint
from tms.api.schemas import Page
from tms.models import Carrier
from tms.services.carrier_adapters import ADAPTERS
from tms.services.runs import OWN_FLEET

router = APIRouter(tags=["carriers"])


class CarrierIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # what Simple WMS is told when it collects: at most 64 there too
    name: str = Field(min_length=1, max_length=64)
    account: str | None = Field(default=None, max_length=40)
    pickup_time: dt.time | None = None
    # how it is booked: "manual" (by hand) is the only one yet
    adapter: str = Field(default="manual", max_length=32)


# PATCH fields that an explicit null clears
CLEARABLE = {"account", "pickup_time"}


class CarrierPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    account: str | None = Field(default=None, max_length=40)
    pickup_time: dt.time | None = None
    adapter: str | None = Field(default=None, max_length=32)


class CarrierOut(BaseModel):
    id: int
    name: str
    account: str | None
    # by the depot's clock, "15:30", or null
    pickup_time: str | None
    adapter: str
    active: bool
    created_at: dt.datetime


def carrier_out(c: Carrier) -> CarrierOut:
    return CarrierOut(id=c.id, name=c.name, account=c.account,
                      pickup_time=c.pickup_time.strftime("%H:%M") if c.pickup_time else None,
                      adapter=c.adapter, active=c.active, created_at=c.created_at)


def get_carrier(db, carrier_id: int) -> Carrier:
    c = db.get(Carrier, carrier_id)
    if c is None:
        raise NotFound(f"no carrier {carrier_id}")
    return c


def _name_free(db, name: str, other_than: int | None = None) -> None:
    """One carrier per name, whatever its case; and never our own trucks."""
    if name.casefold() == OWN_FLEET.casefold():
        raise FieldError("name", f"{OWN_FLEET} is what Simple WMS is told for your own trucks; "
                                 "give the carrier its own name")
    taken = db.execute(select(Carrier.id).where(
        func.lower(Carrier.name) == name.lower(), Carrier.id != (other_than or 0))).first()
    if taken is not None:
        raise FieldError("name", f"carrier {name} already exists")


def _known_adapter(adapter: str | None) -> None:
    if adapter is not None and adapter not in ADAPTERS:
        raise FieldError("adapter", f"{adapter} isn't an adapter Simple TMS has: "
                                    f"{', '.join(ADAPTERS)}")


def _commit(db, name: str) -> None:
    try:
        db.commit()
    except IntegrityError as e:
        # another request took the name between the check and this write
        db.rollback()
        if violated_constraint(e) == "uq_carrier_name":
            raise FieldError("name", f"carrier {name} already exists") from None
        raise


@router.post("/carriers", status_code=201, response_model=CarrierOut)
def create_carrier(body: CarrierIn, db: DB, who: Principal = require("fleet:write")):
    _known_adapter(body.adapter)
    _name_free(db, body.name)
    c = Carrier(name=body.name, account=body.account or None, pickup_time=body.pickup_time,
                adapter=body.adapter, active=True)
    db.add(c)
    _commit(db, body.name)
    return JSONResponse(status_code=201, content=carrier_out(c).model_dump(mode="json"))


@router.get("/carriers", response_model=Page[CarrierOut])
def list_carriers(db: DB, active: bool | None = None, who: Principal = require("runs:read")):
    """By name."""
    q = select(Carrier)
    if active is not None:
        q = q.where(Carrier.active.is_(active))
    rows = db.execute(q.order_by(func.lower(Carrier.name))).scalars().all()
    return Page(items=[carrier_out(c) for c in rows], total=len(rows))


@router.get("/carriers/{id}", response_model=CarrierOut)
def read_carrier(id: int, db: DB, who: Principal = require("runs:read")):
    return carrier_out(get_carrier(db, id))


@router.patch("/carriers/{id}", response_model=CarrierOut)
def change_carrier(id: int, body: CarrierPatch, db: DB, who: Principal = require("fleet:write")):
    """Only what is given changes; `account` or `pickup_time` given as null is
    cleared. A new name is what Simple WMS is told from the next collection on."""
    c = get_carrier(db, id)
    _known_adapter(body.adapter)
    if body.name is not None:
        _name_free(db, body.name, other_than=c.id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "account":
            value = value or None       # an empty account is none
        if value is not None or field in CLEARABLE:
            setattr(c, field, value)
    _commit(db, c.name)
    return carrier_out(c)


@router.post("/carriers/{id}/deactivate", response_model=CarrierOut)
def deactivate_carrier(id: int, db: DB, who: Principal = require("fleet:write")):
    """No new bookings. What is already on its bay stays, to be collected."""
    c = get_carrier(db, id)
    c.active = False
    db.commit()
    return carrier_out(c)


@router.post("/carriers/{id}/reactivate", response_model=CarrierOut)
def reactivate_carrier(id: int, db: DB, who: Principal = require("fleet:write")):
    c = get_carrier(db, id)
    c.active = True
    db.commit()
    return carrier_out(c)
