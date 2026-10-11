"""Runs: a truck's day, its stops in order, and the trip out of the depot."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from tms.api.deps import DB, Principal, require
from tms.api.schemas import Dec, Page
from tms.models import Consignment, OutboundCall, PrintJob, Run
from tms.services import runs
from tms.services.load import consignment_totals

router = APIRouter(tags=["runs"])


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    day: dt.date = Field(alias="date")
    vehicle: str = Field(min_length=1, max_length=12)
    driver: str = Field(min_length=1, max_length=32)


class RunPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vehicle: str | None = Field(default=None, min_length=1, max_length=12)
    driver: str | None = Field(default=None, min_length=1, max_length=32)


class StopConsignment(BaseModel):
    id: int
    wms_ref: str
    customer: str
    suburb: str | None
    status: str
    packages: int
    cartons: int
    weight_kg: Dec
    volume_m3: Dec


class StopOut(BaseModel):
    sequence: int
    consignment: StopConsignment


class LoadOut(BaseModel):
    weight_kg: Dec
    volume_m3: Dec
    max_kg: Dec
    max_m3: Dec
    unknown_weight: int
    unknown_volume: int
    state: str
    message: str | None


class RunVehicle(BaseModel):
    rego: str
    type: str
    max_kg: Dec
    max_m3: Dec
    active: bool


class RunDriver(BaseModel):
    code: str
    name: str


class RunSheetOut(BaseModel):
    job_id: uuid.UUID
    status: str
    printer: str
    last_error: str | None
    created_at: dt.datetime
    printed_at: dt.datetime | None


class ShippingOut(BaseModel):
    pending: int = 0
    done: int = 0
    failed: int = 0


class RunOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    vehicle: RunVehicle
    driver: RunDriver
    created_by: str
    created_at: dt.datetime
    departed_at: dt.datetime | None
    stops: list[StopOut]
    load: LoadOut
    run_sheet: RunSheetOut | None
    shipping: ShippingOut


def stop_consignment(c: Consignment) -> StopConsignment:
    t = consignment_totals(c.packages)
    return StopConsignment(id=c.id, wms_ref=c.wms_ref, customer=c.customer,
                           suburb=(c.ship_to or {}).get("suburb"), status=c.status,
                           packages=t.packages, cartons=t.cartons, weight_kg=t.weight_kg,
                           volume_m3=t.volume_m3)


def run_out(db, run: Run) -> RunOut:
    ld = runs.run_load(run)
    sheet = db.execute(select(PrintJob).where(PrintJob.run_id == run.id)
                       .order_by(PrintJob.id.desc()).limit(1)).scalar_one_or_none()
    calls = dict(db.execute(select(OutboundCall.status, func.count())
                            .where(OutboundCall.run_id == run.id)
                            .group_by(OutboundCall.status)).all())
    v = run.vehicle
    return RunOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle=RunVehicle(rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
                           active=v.active),
        driver=RunDriver(code=run.driver.code, name=run.driver.name),
        created_by=run.created_by, created_at=run.created_at, departed_at=run.departed_at,
        stops=[StopOut(sequence=s.sequence, consignment=stop_consignment(s.consignment))
               for s in run.stops],
        load=LoadOut(weight_kg=ld.weight_kg, volume_m3=ld.volume_m3, max_kg=ld.max_kg,
                     max_m3=ld.max_m3, unknown_weight=ld.unknown_weight,
                     unknown_volume=ld.unknown_volume, state=ld.state, message=ld.message),
        run_sheet=None if sheet is None else RunSheetOut(
            job_id=sheet.job_id, status=sheet.status, printer=sheet.printer,
            last_error=sheet.last_error, created_at=sheet.created_at,
            printed_at=sheet.printed_at),
        shipping=ShippingOut(**calls),
    )


def _fresh(db, run_id: int) -> RunOut:
    db.commit()
    return run_out(db, runs.get_run(db, run_id))


@router.get("/runs", response_model=Page[RunOut])
def list_runs(db: DB, day: dt.date = Query(alias="date"), status: str | None = None,
              who: Principal = require("runs:read")):
    """One day's runs, oldest first. Cancelled ones only when asked for."""
    query = select(Run.id).where(Run.day == day)
    if status:
        query = query.where(Run.status.in_(status.split(",")))
    else:
        query = query.where(Run.status != "cancelled")
    ids = db.execute(query.order_by(Run.id)).scalars().all()
    return Page(items=[run_out(db, runs.get_run(db, i)) for i in ids], total=len(ids))


@router.post("/runs", status_code=201, response_model=RunOut)
def create_run(body: RunIn, db: DB, who: Principal = require("runs:write")):
    run = runs.create(db, day=body.day, rego=body.vehicle, driver_code=body.driver, by=who.name)
    return JSONResponse(status_code=201,
                        content=_fresh(db, run.id).model_dump(mode="json"))


@router.get("/runs/{id}", response_model=RunOut)
def get_run(id: int, db: DB, who: Principal = require("runs:read")):
    return run_out(db, runs.get_run(db, id))


@router.patch("/runs/{id}", response_model=RunOut)
def change_run(id: int, body: RunPatch, db: DB, who: Principal = require("runs:write")):
    runs.change(db, id, rego=body.vehicle, driver_code=body.driver)
    return _fresh(db, id)


@router.post("/runs/{id}/cancel", response_model=RunOut)
def cancel_run(id: int, db: DB, who: Principal = require("runs:write")):
    runs.cancel(db, id)
    return _fresh(db, id)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)
    # 1 is the first stop; left out, it goes last
    position: int | None = Field(default=None, ge=1)


class UnassignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)


class ReorderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_ids: list[int] = Field(max_length=500)
    # the stops in the order the dispatcher drew them: anything else is stale
    seen: list[int] = Field(max_length=500)


@router.post("/runs/{id}/assign", response_model=RunOut)
def assign(id: int, body: AssignIn, db: DB, who: Principal = require("runs:write")):
    runs.assign(db, id, body.consignment_id, body.position, by=who.name)
    return _fresh(db, id)


@router.post("/runs/{id}/unassign", response_model=RunOut)
def unassign(id: int, body: UnassignIn, db: DB, who: Principal = require("runs:write")):
    runs.unassign(db, id, body.consignment_id, by=who.name)
    return _fresh(db, id)


@router.post("/runs/{id}/reorder", response_model=RunOut)
def reorder(id: int, body: ReorderIn, db: DB, who: Principal = require("runs:write")):
    runs.reorder(db, id, body.consignment_ids, body.seen)
    return _fresh(db, id)


@router.post("/runs/{id}/depart", response_model=RunOut)
def depart(id: int, db: DB, who: Principal = require("runs:write")):
    runs.depart(db, id, by=who.name)
    return _fresh(db, id)


@router.post("/runs/{id}/retry-ship", response_model=RunOut)
def retry_ship(id: int, db: DB, who: Principal = require("runs:write")):
    """Ship calls that gave up are tried again from the start, with the same
    message_id each, so Simple WMS still ships each delivery once."""
    runs.get_run(db, id)
    db.execute(update(OutboundCall)
               .where(OutboundCall.run_id == id, OutboundCall.status == "failed")
               .values(status="pending", attempts=0, last_error=None,
                       next_attempt_at=func.now()))
    return _fresh(db, id)
