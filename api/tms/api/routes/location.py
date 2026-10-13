"""Live location: the driver's phone says where the truck is while the run is
on the road; the office sees each truck's latest position."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from tms.api.deps import DB, Principal, require
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
                  who: Principal = require("deliveries:write")):
    """Where the run's phone is now, about every 30 seconds while the run is
    on the road. Not a confirmation: no message_id, nothing queued, and a
    lost one is simply followed by the next."""
    location.record(db, who, id, at=body.at, latitude=body.latitude,
                    longitude=body.longitude, accuracy_m=body.accuracy_m)
    db.commit()
    return Response(status_code=204)
