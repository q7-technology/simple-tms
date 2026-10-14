"""When a run should reach each stop left, and the depot: estimates from the
stop order and the driver's average time per stop so far on the run, from
leaving the depot to the latest stop done. No routing service and no
distances: the average already has the driving in it. Until the first stop
is done, a stop takes TMS_ETA_MINUTES_PER_STOP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# however quick the drops, nothing is due sooner than this apart
SHORTEST = timedelta(minutes=1)


@dataclass(frozen=True)
class Estimate:
    # average (from the stops done) or default (none done yet)
    basis: str
    per_stop: timedelta
    # each stop left, in order: (consignment id, when it should be reached)
    stops: list[tuple[int, datetime]]
    # the drive back counts as one stop more
    back_at_depot: datetime


def estimate(*, departed_at: datetime, done_at: list[datetime], left: list[int], now: datetime,
             default: timedelta) -> Estimate:
    """`done_at`: when each stop done so far was done (delivered or not);
    `left`: the stops still to do, in order. Nothing is due before `now`: a
    driver running late is due now, and every stop after moves with it."""
    if done_at:
        last = max(done_at)
        per, basis = (last - departed_at) / len(done_at), "average"
    else:
        last, per, basis = departed_at, default, "default"
    per = max(per, SHORTEST)
    due = max(last + per, now)
    stops = []
    for consignment_id in left:
        stops.append((consignment_id, due))
        due += per
    return Estimate(basis=basis, per_stop=per, stops=stops, back_at_depot=due)
