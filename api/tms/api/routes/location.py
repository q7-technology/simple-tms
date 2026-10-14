"""Live location: the driver's phone says where the truck is while the run is
on the road; the office sees each truck's latest position."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from tms.api.deps import DB, Principal, require
from tms.api.schemas import Page
from tms.config import get_settings
from tms.services import location

router = APIRouter(tags=["location"])


class PositionIn(BaseModel):
    """As the phone has it, in decimal strings ("-37.562210"); more than six
    places is rounded, never refused."""

    model_config = ConfigDict(extra="forbid")
    at: AwareDatetime
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    accuracy_m: Decimal = Field(ge=0)

    @field_validator("accuracy_m")
    @classmethod
    def _close_enough(cls, value: Decimal) -> Decimal:
        if value > location.MAX_ACCURACY_M:
            raise ValueError("a position rougher than 1,000 m isn't kept")
        return value


@router.post("/runs/{id}/position", status_code=204)
def send_position(id: int, body: PositionIn, db: DB,
                  who: Principal = require("location:write")):
    """Where the run's phone is now, about every 30 seconds while the run is
    on the road. location:write: the run's driver, or a key made with it
    named (a tracker in the truck), for any run on the road. Not a confirmation: no message_id, nothing queued, and a
    lost one is simply followed by the next."""
    location.record(db, who, id, at=body.at, latitude=body.latitude,
                    longitude=body.longitude, accuracy_m=body.accuracy_m)
    db.commit()
    return Response(status_code=204)


class TruckAt(BaseModel):
    run_id: int
    run_no: str
    vehicle: dict
    driver: dict
    # decimal strings, as sent
    latitude: str
    longitude: str
    accuracy_m: str
    # when the phone took it, and when it arrived ("last heard")
    at: dt.datetime
    received_at: dt.datetime


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


@router.get("/positions", response_model=Page[TruckAt])
def where_trucks_are(db: DB, day: dt.date = Query(alias="date"),
                     who: Principal = require("location:read")):
    """The latest position of each of the day's runs on the road, by run.
    Before Start and once a run is finished, it has none."""
    rows = location.latest(db, day)
    return Page(items=[TruckAt(
        run_id=run.id, run_no=run.number,
        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
        driver={"code": run.driver.code, "name": run.driver.name},
        latitude=_plain(p.latitude), longitude=_plain(p.longitude),
        accuracy_m=_plain(p.accuracy_m), at=p.recorded_at, received_at=p.received_at)
        for run, p in rows], total=len(rows))


class MapOut(BaseModel):
    # a raster tile address with {z}, {x} and {y}, or null: no map, a list only
    tile_url: str | None
    attribution: str | None


@router.get("/map", response_model=MapOut)
def map_tiles(who: Principal = require("location:read")):
    """Where the desktop's map tiles come from (TMS_MAP_TILE_URL)."""
    s = get_settings()
    return MapOut(tile_url=s.map_tile_url or None, attribution=s.map_attribution or None)
