"""ETAs from the stop order and the driver's average time per stop so far,
labelled as estimates. No routing service: distance doesn't come into it."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from deliveries_samples import delivered, failed, on_the_road, planned_run
from tms.models import Run
from tms.services.eta import estimate

T = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
TWENTY = 20 * MIN


def at(minutes: int) -> datetime:
    return T + minutes * MIN


def test_before_the_first_stop_each_stop_takes_the_default():
    e = estimate(departed_at=T, done_at=[], left=[31, 32], now=at(5), default=TWENTY)
    assert (e.basis, e.per_stop) == ("default", TWENTY)
    assert e.stops == [(31, at(20)), (32, at(40))]
    # the drive back counts as one stop more
    assert e.back_at_depot == at(60)


def test_after_a_stop_or_two_the_drivers_own_average():
    e = estimate(departed_at=T, done_at=[at(30), at(60)], left=[33, 34], now=at(61),
                 default=TWENTY)
    assert (e.basis, e.per_stop) == ("average", 30 * MIN)
    assert e.stops == [(33, at(90)), (34, at(120))]
    assert e.back_at_depot == at(150)


def test_a_driver_running_late_is_never_due_before_now():
    e = estimate(departed_at=T, done_at=[at(10)], left=[32, 33], now=at(45), default=TWENTY)
    assert e.per_stop == 10 * MIN
    assert e.stops == [(32, at(45)), (33, at(55))]
    assert e.back_at_depot == at(65)


def test_every_stop_done_leaves_the_drive_back():
    e = estimate(departed_at=T, done_at=[at(25), at(50)], left=[], now=at(52), default=TWENTY)
    assert e.stops == [] and e.back_at_depot == at(75)


def test_quick_drops_still_take_a_minute_each():
    e = estimate(departed_at=T, done_at=[T + timedelta(seconds=20)], left=[32], now=at(1),
                 default=TWENTY)
    assert e.per_stop == MIN


# ---- on the run, as the desktop reads it ---------------------------------------

def test_a_run_on_the_road_carries_its_estimates(client, db, dispatcher_headers):
    run, (a, b, c), kim = on_the_road(client, db, dispatcher_headers, n=3)
    db.execute(update(Run).values(departed_at=datetime.now(UTC) - 40 * MIN))
    db.commit()
    delivered(client, kim, run["id"], a, at=(datetime.now(UTC) - 20 * MIN).isoformat())
    failed(client, kim, run["id"], b)
    out = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["estimate"]
    # two stops in 40 minutes, the one that couldn't be delivered too
    assert (out["basis"], out["minutes_per_stop"]) == ("average", 20)
    (nxt,) = out["stops"]
    assert (nxt["consignment_id"], nxt["sequence"]) == (c.id, 3)
    due = datetime.fromisoformat(nxt["at"])
    assert abs(due - (datetime.now(UTC) + 20 * MIN)) < MIN
    back = datetime.fromisoformat(out["back_at_depot"]) - due
    assert abs(back - 20 * MIN) < timedelta(seconds=1)


def test_a_run_not_on_the_road_has_none(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    assert client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["estimate"] is None
