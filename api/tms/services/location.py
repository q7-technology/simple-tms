"""Live location: where each truck on the road is, from its driver's phone.
Shared only while a run is on the road (from Start until it is finished),
and kept only for the retention period: positions are personal information,
not business history, so they never go in a consignment's history."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, Refused
from tms.models import Position, Run
from tms.services import deliveries, fleet
from tms.services.deliveries import MICRO, TENTH

# the phone sends one about every 30 s; more often than this is a fault
MIN_GAP = timedelta(seconds=10)
# a fix rougher than this would put the truck in the wrong suburb
MAX_ACCURACY_M = Decimal(1000)

NOT_SHARED = {"planned": ("run_not_started", "hasn't left yet"),
              "done": ("run_done", "is finished"),
              "cancelled": ("run_cancelled", "was cancelled")}


def stale(run: Run) -> bool:
    """A run left on the road from an earlier day: its day is before the
    server's (UTC). UTC is behind Australia, which is the grace: a run that
    goes past midnight still shares until 10 or 11 am the day after by the
    Australian clock, and a forgotten one stops then rather than sharing all
    of the next working day."""
    return run.day < fleet.today()


def record(db: Session, who, run_id: int, *, at: datetime, latitude: Decimal,
           longitude: Decimal, accuracy_m: Decimal) -> Position:
    """Where the run's phone is now. Only for a run on the road: before Start
    and after it is finished nothing is kept. The time is the phone's, but
    never later than now."""
    run = deliveries.own_run(db, who, run_id)
    if run.status in NOT_SHARED:
        code, said = NOT_SHARED[run.status]
        raise Conflict(code, f"{run.number} {said}, so where it is isn't shared")
    if stale(run):
        raise Conflict("run_stale", f"{run.number} was for {fleet.long_date(run.day)} and was "
                                    "left open, so where it is isn't shared. Finish it.")
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


def latest(db: Session, day: date) -> list[tuple[Run, Position]]:
    """Each of the day's runs on the road, with the latest position its phone
    sent. A run not on the road shows none, whatever was kept from it."""
    # the newest row of each run is its latest: rows are numbered as they arrive
    newest = (select(func.max(Position.id)).join(Run, Run.id == Position.run_id)
              .where(Run.day == day, Run.status == "departed").group_by(Position.run_id))
    rows = db.execute(select(Position).where(Position.id.in_(newest))
                      .order_by(Position.run_id)).scalars().all()
    runs = {r.id: r for r in db.execute(select(Run).where(
        Run.id.in_([p.run_id for p in rows]))).scalars()}
    return [(runs[p.run_id], p) for p in rows]
