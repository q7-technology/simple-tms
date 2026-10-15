"""Runs: a truck's day, its stops in order, and the trip out of the depot."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import aliased

from tms.api.deps import DB, Principal, require
from tms.api.schemas import Dec, Page
from tms.config import get_settings
from tms.models import Consignment, Manifest, OutboundCall, Pod, PrintJob, Run
from tms.services import eta, fleet, records, runs
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
    # what the driver recorded: delivered or failed (with the reason), or null
    outcome: str | None = None
    outcome_at: dt.datetime | None = None
    reason: str | None = None


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
    # why the truck can't take this run (grounded, or overdue on its day), or null
    blocked: dict | None = None


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


class HeldByOut(BaseModel):
    """An earlier call, on another run or manifest, that holds later calls
    for its consignment until it is done."""
    kind: str
    run_no: str | None
    manifest_no: str | None
    wms_ref: str
    # pending (still being tried) or failed (gave up: Try again starts it)
    status: str
    last_error: str | None


class ShippingOut(BaseModel):
    pending: int = 0
    done: int = 0
    failed: int = 0
    # why the latest call that gave up did, or null
    last_error: str | None = None
    # how many of the pending wait, untried, on an earlier call for their
    # consignment on another run or manifest; and those calls, oldest first
    held: int = 0
    held_by: list[HeldByOut] = []


def _first_open():
    """The earliest call not yet done for the same consignment, before this
    one: the one the worker waits on (see the worker's _in_turn)."""
    earlier = aliased(OutboundCall)
    return (select(func.min(earlier.id))
            .where(earlier.consignment_id == OutboundCall.consignment_id,
                   earlier.id < OutboundCall.id, earlier.status != "done")
            .scalar_subquery())


def shipping_out(db, *where) -> ShippingOut:
    """A run's or a manifest's calls to Simple WMS by state, the latest
    error of one that gave up, and what holds those waiting their turn."""
    calls = dict(db.execute(select(OutboundCall.status, func.count()).where(*where)
                            .group_by(OutboundCall.status)).all())
    error = db.execute(select(OutboundCall.last_error).where(*where, OutboundCall.status == "failed")
                       .order_by(OutboundCall.id.desc())
                       .limit(1)).scalar_one_or_none()
    waiting = db.execute(select(OutboundCall.run_id, OutboundCall.manifest_id, _first_open())
                         .where(*where, OutboundCall.status == "pending")).all()
    firsts = {first for _, _, first in waiting if first is not None}
    holders = {call.id: (call, run, manifest, ref) for call, run, manifest, ref in db.execute(
        select(OutboundCall, Run, Manifest, Consignment.wms_ref)
        .join(Consignment, Consignment.id == OutboundCall.consignment_id)
        .outerjoin(Run, Run.id == OutboundCall.run_id)
        .outerjoin(Manifest, Manifest.id == OutboundCall.manifest_id)
        .where(OutboundCall.id.in_(firsts)))} if firsts else {}
    # one held by an earlier call of its own run or manifest is shown there already
    held = [first for run_id, manifest_id, first in waiting if first is not None
            and (holders[first][0].run_id, holders[first][0].manifest_id) != (run_id, manifest_id)]
    return ShippingOut(**calls, last_error=error, held=len(held), held_by=[
        HeldByOut(kind=call.kind, run_no=run and run.number,
                  manifest_no=manifest and manifest.number, wms_ref=ref,
                  status=call.status, last_error=call.last_error)
        for call, run, manifest, ref in (holders[i] for i in sorted(set(held)))])


def retry_calls(db, owner: str, owner_id: int) -> None:
    """Start again, from scratch and with the same message_id each, the calls
    of a run or manifest (`owner` run_id or manifest_id) that gave up, and
    the earlier calls that gave up and hold its calls still to make. Done
    and pending calls are left as they are."""
    later = aliased(OutboundCall)
    holds_ours = exists().where(later.consignment_id == OutboundCall.consignment_id,
                                later.id > OutboundCall.id, later.status != "done",
                                getattr(later, owner) == owner_id)
    db.execute(update(OutboundCall)
               .where(OutboundCall.status == "failed",
                      or_(getattr(OutboundCall, owner) == owner_id, holds_ours))
               .values(status="pending", attempts=0, last_error=None,
                       next_attempt_at=func.now()))


class StopEta(BaseModel):
    consignment_id: int
    sequence: int
    at: dt.datetime


class EstimateOut(BaseModel):
    """Estimates, and to be shown as such: from the stop order and the
    driver's average time per stop so far on this run."""

    # average (from the stops done so far) or default (none done yet)
    basis: str
    minutes_per_stop: int
    stops: list[StopEta]
    back_at_depot: dt.datetime


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
    finished_at: dt.datetime | None
    stops: list[StopOut]
    load: LoadOut
    run_sheet: RunSheetOut | None
    # its ship calls to Simple WMS, made when it left
    shipping: ShippingOut
    # its return calls to Simple WMS, made when it was finished with
    # something that couldn't be delivered
    returns: ShippingOut
    # the latest pre-start of its truck for it: { id, at, result }, or null
    prestart: dict | None = None
    # while it is on the road: when it should reach each stop left, and the depot
    estimate: EstimateOut | None = None


def run_estimate(run: Run) -> EstimateOut | None:
    if run.status != "departed" or run.departed_at is None:
        return None
    left = [s for s in run.stops if s.outcome is None]
    e = eta.estimate(departed_at=run.departed_at,
                     done_at=[s.outcome_at for s in run.stops if s.outcome_at is not None],
                     left=[s.consignment_id for s in left], now=dt.datetime.now(dt.UTC),
                     default=dt.timedelta(minutes=get_settings().eta_minutes_per_stop))
    return EstimateOut(basis=e.basis, minutes_per_stop=round(e.per_stop.total_seconds() / 60),
                       stops=[StopEta(consignment_id=s.consignment_id, sequence=s.sequence, at=due)
                              for s, (_, due) in zip(left, e.stops)],
                       back_at_depot=e.back_at_depot)


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
    reasons = dict(db.execute(select(Pod.stop_id, Pod.reason).where(
        Pod.run_id == run.id, Pod.outcome == "failed")).all())
    v = run.vehicle
    why = fleet.standing(db, v, run.day).blocked
    check = records.run_check(db, run)
    return RunOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle=RunVehicle(rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
                           active=v.active,
                           blocked=None if why is None else {"code": why.code,
                                                             "message": why.message}),
        prestart=None if check is None else {"id": check.id, "at": check.at,
                                             "result": check.result},
        driver=RunDriver(code=run.driver.code, name=run.driver.name),
        created_by=run.created_by, created_at=run.created_at, departed_at=run.departed_at,
        finished_at=run.finished_at,
        stops=[StopOut(sequence=s.sequence, consignment=stop_consignment(s.consignment),
                       outcome=s.outcome, outcome_at=s.outcome_at, reason=reasons.get(s.id))
               for s in run.stops],
        load=LoadOut(weight_kg=ld.weight_kg, volume_m3=ld.volume_m3, max_kg=ld.max_kg,
                     max_m3=ld.max_m3, unknown_weight=ld.unknown_weight,
                     unknown_volume=ld.unknown_volume, state=ld.state, message=ld.message),
        run_sheet=None if sheet is None else RunSheetOut(
            job_id=sheet.job_id, status=sheet.status, printer=sheet.printer,
            last_error=sheet.last_error, created_at=sheet.created_at,
            printed_at=sheet.printed_at),
        shipping=shipping_out(db, OutboundCall.run_id == run.id, OutboundCall.kind == "ship"),
        returns=shipping_out(db, OutboundCall.run_id == run.id, OutboundCall.kind == "return"),
        estimate=run_estimate(run),
    )


def _fresh(db, run_id: int) -> RunOut:
    db.commit()
    return run_out(db, runs.get_run(db, run_id))


@router.get("/runs", response_model=Page[RunOut])
def list_runs(db: DB, day: dt.date = Query(alias="date"), status: str | None = None,
              left_open: bool = False, who: Principal = require("runs:read")):
    """One day's runs, oldest first. Cancelled ones only when asked for. With
    left_open, first the runs from before that day still on the road: left
    and never finished, for the office to finish."""
    query = select(Run.id).where(Run.day == day)
    if status:
        query = query.where(Run.status.in_(status.split(",")))
    else:
        query = query.where(Run.status != "cancelled")
    if left_open:
        query = query.union(select(Run.id).where(Run.day < day, Run.status == "departed"))
    ids = db.execute(select(Run.id).where(Run.id.in_(query))
                     .order_by(Run.day, Run.id)).scalars().all()
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
    """Ship and return calls that gave up are tried again from the start,
    with the same message_id each, so Simple WMS still acts on each once;
    and so are earlier calls that gave up and hold this run's."""
    runs.get_run(db, id)
    retry_calls(db, "run_id", id)
    return _fresh(db, id)
