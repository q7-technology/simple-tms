"""The driver app's routes: today's runs, a run loaded onto the phone, and
the confirmations from the road. Every confirmation carries a message_id; a
repeat gets the first reply back and changes nothing."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tms.api.deps import DB, Principal, require, require_any
from tms.api.errors import Forbidden
from tms.api.schemas import Dec
from tms.models import Run
from tms.services import checklists, deliveries, fleet, records
from tms.services.envelope import handle

router = APIRouter(tags=["deliveries"])


# ---- reading -----------------------------------------------------------------

class DriverRunOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    # rego, type, its last odometer reading, and why it can't go (or null)
    vehicle: dict
    stops: int
    packages: int
    delivered: int
    failed: int
    # the latest pre-start of this truck for this run: { id, at, result }, or null
    prestart: dict | None
    # the truck's checklist: [{ id, label, critical }]
    checklist: list[dict]


class PackageLine(BaseModel):
    package_no: int
    type: str
    weight_kg: Dec | None
    sscc: str | None


class StopDetail(BaseModel):
    sequence: int
    consignment_id: int
    wms_ref: str
    customer: str
    ship_to: dict
    packages: list[PackageLine]
    outcome: str | None
    outcome_at: dt.datetime | None


class RunStopsOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    vehicle: dict
    driver: dict
    departed_at: dt.datetime | None
    finished_at: dt.datetime | None
    stops: list[StopDetail]


def driver_run_out(db, run: Run) -> DriverRunOut:
    v = run.vehicle
    s = fleet.standing(db, v, run.day)
    why = s.blocked
    check = records.run_check(db, run)
    return DriverRunOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle={"rego": v.rego, "type": v.type, "odometer_km": s.odometer_km,
                 "blocked": None if why is None else {"code": why.code, "message": why.message}},
        stops=len(run.stops), packages=sum(len(s.consignment.packages) for s in run.stops),
        delivered=sum(s.outcome == "delivered" for s in run.stops),
        failed=sum(s.outcome == "failed" for s in run.stops),
        prestart=None if check is None else {"id": check.id, "at": check.at,
                                             "result": check.result},
        checklist=[{"id": i.id, "label": i.label, "critical": i.critical}
                   for i in checklists.live(db, v.type)])


def run_stops_out(run: Run) -> RunStopsOut:
    return RunStopsOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
        driver={"code": run.driver.code, "name": run.driver.name},
        departed_at=run.departed_at, finished_at=run.finished_at,
        stops=[StopDetail(
            sequence=s.sequence, consignment_id=s.consignment_id, wms_ref=s.consignment.wms_ref,
            customer=s.consignment.customer, ship_to=s.consignment.ship_to or {},
            packages=[PackageLine(package_no=p.package_no, type=p.type, weight_kg=p.weight_kg,
                                  sscc=p.sscc) for p in s.consignment.packages],
            outcome=s.outcome, outcome_at=s.outcome_at) for s in run.stops])


@router.get("/driver/runs", response_model=list[DriverRunOut])
def my_runs(db: DB, day: dt.date = Query(alias="date"),
            who: Principal = require("deliveries:read")):
    """The signed-in driver's runs for the day (the phone's own date), and any
    of theirs that left on an earlier day and were never finished."""
    if who.kind != "driver":
        raise Forbidden("only a driver has runs of their own")
    return [driver_run_out(db, r) for r in deliveries.driver_runs(db, who.id, day)]


@router.get("/runs/{id}/stops", response_model=RunStopsOut)
def run_stops(id: int, db: DB, who: Principal = require_any("runs:read", "deliveries:read")):
    """The whole run as the phone keeps it: each stop's address, packages and
    outcome. A driver sees only their own runs."""
    return run_stops_out(deliveries.own_run(db, who, id))


# ---- confirmations -----------------------------------------------------------

class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: uuid.UUID


class StartOut(BaseModel):
    message_id: uuid.UUID
    run_id: int
    run_no: str
    status: str
    departed_at: dt.datetime | None


@router.post("/runs/{id}/start", response_model=StartOut)
def start_run(id: int, body: MessageIn, db: DB, who: Principal = require("deliveries:write")):
    def work():
        run = deliveries.start(db, who, id)
        return StartOut(message_id=body.message_id, run_id=run.id, run_no=run.number,
                        status=run.status, departed_at=run.departed_at)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/start", work)


class Position(BaseModel):
    """Where the phone was, as decimal strings ("-37.562210"); more than six
    places is rounded, never refused."""

    model_config = ConfigDict(extra="forbid")
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    accuracy_m: Decimal | None = Field(default=None, ge=0, le=1_000_000)

    def where(self) -> deliveries.Where:
        return deliveries.Where(self.latitude, self.longitude, self.accuracy_m)


class PackageTick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_no: int = Field(ge=1)
    how: Literal["scan", "tick"]


class DeliverIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: uuid.UUID
    at: AwareDatetime
    packages: list[PackageTick] = Field(max_length=500)
    signed_name: str | None = Field(default=None, max_length=120)
    signature_file: uuid.UUID | None = None
    photo_file: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)
    position: Position | None = None

    @model_validator(mode="after")
    def _proof(self):
        if self.signature_file and not self.signed_name:
            raise ValueError("a signature needs the printed name with it")
        if self.signed_name and not self.signature_file:
            raise ValueError("a printed name needs the signature with it")
        if not self.signature_file and not self.photo_file:
            raise ValueError("a delivery needs a signature with a printed name, a photo, or both")
        return self


class FailIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: uuid.UUID
    at: AwareDatetime
    reason: Literal["closed", "refused", "damaged", "wrong_address"]
    note: str | None = Field(default=None, max_length=500)
    photo_file: uuid.UUID | None = None
    position: Position | None = None


class StopDoneOut(BaseModel):
    message_id: uuid.UUID
    run_id: int
    run_no: str
    consignment_id: int
    sequence: int
    outcome: str
    at: dt.datetime
    pod_id: int
    done: int
    left: int


def _stop_done(db, message_id, run, stop, pod) -> StopDoneOut:
    t = deliveries.tally(db, run.id)
    return StopDoneOut(message_id=message_id, run_id=run.id, run_no=run.number,
                       consignment_id=stop.consignment_id, sequence=stop.sequence,
                       outcome=pod.outcome, at=pod.at, pod_id=pod.id,
                       done=t.get("delivered", 0) + t.get("failed", 0), left=t.get(None, 0))


@router.post("/runs/{id}/stops/{consignment_id}/deliver", response_model=StopDoneOut)
def deliver(id: int, consignment_id: int, body: DeliverIn, db: DB,
            who: Principal = require("deliveries:write")):
    def work():
        run, stop, pod = deliveries.deliver(
            db, who, id, consignment_id, message_id=body.message_id, at=body.at,
            packages=[p.model_dump() for p in body.packages], signed_name=body.signed_name,
            signature_file=body.signature_file, photo_file=body.photo_file, note=body.note,
            where=body.position.where() if body.position else None)
        return _stop_done(db, body.message_id, run, stop, pod)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/stops/{consignment_id}/deliver", work)


@router.post("/runs/{id}/stops/{consignment_id}/fail", response_model=StopDoneOut)
def fail(id: int, consignment_id: int, body: FailIn, db: DB,
         who: Principal = require("deliveries:write")):
    def work():
        run, stop, pod = deliveries.fail(
            db, who, id, consignment_id, message_id=body.message_id, at=body.at,
            reason=body.reason, note=body.note, photo_file=body.photo_file,
            where=body.position.where() if body.position else None)
        return _stop_done(db, body.message_id, run, stop, pod)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/stops/{consignment_id}/fail", work)


class FinishOut(BaseModel):
    message_id: uuid.UUID
    run_id: int
    run_no: str
    status: str
    finished_at: dt.datetime | None
    delivered: int
    failed: int


@router.post("/runs/{id}/finish", response_model=FinishOut)
def finish_run(id: int, body: MessageIn, db: DB,
               who: Principal = require_any("deliveries:write", "runs:write")):
    """The driver back at the depot, or the office for them (runs:write)."""
    def work():
        run = deliveries.finish(db, who, id)
        t = deliveries.tally(db, run.id)
        return FinishOut(message_id=body.message_id, run_id=run.id, run_no=run.number,
                         status=run.status, finished_at=run.finished_at,
                         delivered=t.get("delivered", 0), failed=t.get("failed", 0))
    return handle(db, who, body.message_id, f"/v1/runs/{id}/finish", work)
