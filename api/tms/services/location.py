"""Live location: where each truck on the road is, from its driver's phone.
Shared only while a run is on the road (from Start until it is finished),
and kept only for the retention period: positions are personal information,
not business history, so they never go in a consignment's history."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, Refused
from tms.models import Position
from tms.services import deliveries
from tms.services.deliveries import MICRO, TENTH

# the phone sends one about every 30 s; more often than this is a fault
MIN_GAP = timedelta(seconds=10)
# a fix rougher than this would put the truck in the wrong suburb
MAX_ACCURACY_M = Decimal(1000)

NOT_SHARED = {"planned": ("run_not_started", "hasn't left yet"),
              "done": ("run_done", "is finished"),
              "cancelled": ("run_cancelled", "was cancelled")}


def record(db: Session, who, run_id: int, *, at: datetime, latitude: Decimal,
           longitude: Decimal, accuracy_m: Decimal) -> Position:
    """Where the run's phone is now. Only for a run on the road: before Start
    and after it is finished nothing is kept. The time is the phone's, but
    never later than now."""
    run = deliveries.own_run(db, who, run_id)
    if run.status in NOT_SHARED:
        code, said = NOT_SHARED[run.status]
        raise Conflict(code, f"{run.number} {said}, so where it is isn't shared")
    now = datetime.now(UTC)
    last = db.execute(select(func.max(Position.received_at))
                      .where(Position.run_id == run.id)).scalar()
    if last is not None and now - last < MIN_GAP:
        raise Refused(429, "too_often", "one position every 10 seconds is plenty")
    row = Position(run_id=run.id, driver_id=run.driver_id, vehicle_id=run.vehicle_id,
                   latitude=latitude.quantize(MICRO), longitude=longitude.quantize(MICRO),
                   accuracy_m=accuracy_m.quantize(TENTH), recorded_at=min(at, now),
                   received_at=now)
    db.add(row)
    db.flush()
    return row
