"""The driver app's routes: today's runs, a run loaded onto the phone, and
the confirmations from the road. Every confirmation carries a message_id; a
repeat gets the first reply back and changes nothing."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from tms.api.deps import DB, Principal, require, require_any
from tms.api.errors import Forbidden
from tms.api.schemas import Dec
from tms.models import Run
from tms.services import deliveries
from tms.services.envelope import handle

router = APIRouter(tags=["deliveries"])


# ---- reading -----------------------------------------------------------------

class DriverRunOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    vehicle: dict
    stops: int
    packages: int
    delivered: int
    failed: int


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


def driver_run_out(run: Run) -> DriverRunOut:
    return DriverRunOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
        stops=len(run.stops), packages=sum(len(s.consignment.packages) for s in run.stops),
        delivered=sum(s.outcome == "delivered" for s in run.stops),
        failed=sum(s.outcome == "failed" for s in run.stops))


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
    return [driver_run_out(r) for r in deliveries.driver_runs(db, who.id, day)]


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
