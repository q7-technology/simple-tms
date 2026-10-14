"""The driver's phone says where the truck is, about every 30 seconds, from
Start until the run is finished, and never outside a run."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from conftest import bearer
from deliveries_samples import delivered, driver_headers, on_the_road, planned_run
from runs_samples import DAY, driver
from test_api_keys import _key
from tms.models import ConsignmentEvent, Position

# a run of an earlier day takes no positions: the samples' DAY is today
pytestmark = pytest.mark.usefixtures("today_is_day")


def where(**over) -> dict:
    return {"at": datetime.now(UTC).isoformat(), "latitude": "-37.5622104",
            "longitude": "143.8503", "accuracy_m": "12.34", **over}


def _older(db, seconds=11):
    """The positions kept so far arrived this long ago."""
    db.execute(update(Position).values(received_at=Position.received_at - timedelta(seconds=seconds)))
    db.commit()


def send(client, headers, run_id, **over):
    return client.post(f"/v1/runs/{run_id}/position", headers=headers, json=where(**over))


def test_the_phone_says_where_the_truck_is_while_the_run_is_on_the_road(client, db,
                                                                        dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    events = db.execute(select(ConsignmentEvent.id)).scalars().all()
    r = send(client, kim, run["id"])
    assert r.status_code == 204, r.text
    p = db.execute(select(Position)).scalar_one()
    assert p.run_id == run["id"] and p.driver_id == 1 and p.vehicle_id == 1
    assert (str(p.latitude), str(p.longitude), str(p.accuracy_m)) == ("-37.562210", "143.850300", "12.3")
    assert p.received_at >= p.recorded_at
    # a position is never history: nothing is added to a consignment's
    assert db.execute(select(ConsignmentEvent.id)).scalars().all() == events


def test_nothing_is_shared_before_the_run_leaves(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    r = send(client, driver_headers(client), run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "run_not_started",
        "detail": f"{run['run_no']} hasn't left yet, so where it is isn't shared"}
    assert db.execute(select(Position)).first() is None


def test_nothing_is_shared_once_the_run_is_finished(client, db, dispatcher_headers):
    run, (a,), kim = on_the_road(client, db, dispatcher_headers, n=1)
    delivered(client, kim, run["id"], a)
    finish = client.post(f"/v1/runs/{run['id']}/finish", headers=kim,
                         json={"message_id": str(uuid.uuid4())})
    assert finish.status_code == 200, finish.text
    r = send(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "run_done", "detail": f"{run['run_no']} is finished, so where it is isn't shared"}
    assert db.execute(select(Position)).first() is None


def test_another_drivers_run_is_not_found(client, db, dispatcher_headers):
    run, _, _ = on_the_road(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    r = send(client, driver_headers(client, code="mel"), run["id"])
    assert r.status_code == 404


def test_a_dispatcher_cant_send_one(client, db, dispatcher_headers):
    run, _, _ = on_the_road(client, db, dispatcher_headers)
    assert send(client, dispatcher_headers, run["id"]).status_code == 403


def test_positions_have_a_scope_of_their_own(client, db, dispatcher_headers, capsys):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    me = client.get("/v1/auth/me", headers=kim).json()
    assert "location:write" in me["scopes"]
    # a key that records deliveries is not a tracker
    handheld = bearer(_key(capsys, "handhelds", "deliveries:read,deliveries:write"))
    r = send(client, handheld, run["id"])
    assert r.status_code == 403 and "location:write" in r.json()["detail"]
    # a tracker is a key made with location:write named, for any run on the road
    tracker = bearer(_key(capsys, "tracker", "location:write"))
    assert send(client, tracker, run["id"]).status_code == 204
    _older(db)
    assert send(client, kim, run["id"]).status_code == 204


def test_a_position_too_rough_to_draw_is_refused(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    r = send(client, kim, run["id"], accuracy_m="1500")
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "accuracy_m"
    assert send(client, kim, run["id"], accuracy_m=None).status_code == 422


def test_a_time_ahead_of_now_is_taken_as_now(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    ahead = datetime.now(UTC) + timedelta(hours=3)
    assert send(client, kim, run["id"], at=ahead.isoformat()).status_code == 204
    p = db.execute(select(Position)).scalar_one()
    assert p.recorded_at <= p.received_at


def test_one_position_every_ten_seconds_is_plenty(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    assert send(client, kim, run["id"]).status_code == 204
    r = send(client, kim, run["id"])
    assert r.status_code == 429 and r.json()["code"] == "too_often"
    # ten seconds on, the next is kept
    db.execute(update(Position).values(received_at=Position.received_at - timedelta(seconds=11)))
    db.commit()
    assert send(client, kim, run["id"]).status_code == 204
    assert len(db.execute(select(Position)).all()) == 2


def test_a_run_left_open_from_an_earlier_day_shares_nothing(client, db, dispatcher_headers):
    # the server's day is UTC, behind Australia's: yesterday's run (by the
    # server) is refused, which in Australia is from 10 or 11 am the day after
    run, _, kim = on_the_road(client, db, dispatcher_headers, day=DAY - timedelta(days=1))
    r = send(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "run_stale",
        "detail": f"{run['run_no']} was for 13 Oct 2026 and was left open, "
                  "so where it is isn't shared. Finish it."}
    assert db.execute(select(Position)).first() is None


def test_a_run_of_today_by_the_server_still_shares(client, db, dispatcher_headers):
    # an Australian morning is still the day before by the server's clock
    run, _, kim = on_the_road(client, db, dispatcher_headers, day=DAY)
    assert send(client, kim, run["id"]).status_code == 204



def test_a_position_waits_its_turn_behind_another_for_the_run(client, db, engine,
                                                               dispatcher_headers):
    """Two at once can't both get past the 10-second check: the second waits
    for the first, then sees it."""
    from sqlalchemy import text

    from conftest import blocked_on_a_lock, fresh_client, in_background
    from tms.services import location

    run, _, kim = on_the_road(client, db, dispatcher_headers)
    with engine.connect() as other:
        # the first, part way through: holding the run's turn, its row not yet in
        other.execute(text("select pg_advisory_xact_lock(:a, :b)"),
                      {"a": location.POSITION_LOCK, "b": run["id"]})

        def post():
            with fresh_client() as c:
                return send(c, kim, run["id"]).status_code

        second = in_background(post)
        assert blocked_on_a_lock(engine, second), "the second didn't wait its turn"
        other.execute(text(
            "insert into position (run_id, driver_id, vehicle_id, latitude, longitude, "
            "accuracy_m, recorded_at, received_at) values (:r, 1, 1, -37.5, 143.8, 9, now(), now())"),
            {"r": run["id"]})
        other.commit()
    assert second.result(timeout=10) == 429


def test_positions_sent_all_at_once_still_keep_only_one(client, db, dispatcher_headers):
    from conftest import all_at_once, fresh_client

    run, _, kim = on_the_road(client, db, dispatcher_headers)

    def post():
        with fresh_client() as c:
            return send(c, kim, run["id"]).status_code

    codes = all_at_once(8, post)
    assert sorted(codes) == [204] + [429] * 7
    assert len(db.execute(select(Position)).all()) == 1


def test_a_position_from_long_ago_or_before_the_run_left_is_refused(client, db,
                                                                    dispatcher_headers):
    from tms.models import Run

    run, _, kim = on_the_road(client, db, dispatcher_headers)
    now = datetime.now(UTC)
    # more than 10 minutes old: only the newest is ever sent
    r = send(client, kim, run["id"], at=(now - timedelta(minutes=11)).isoformat())
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "at"
    # from before the run left (a minute's slack for the phone's clock)
    db.execute(update(Run).where(Run.id == run["id"]).values(departed_at=now - timedelta(minutes=5)))
    db.commit()
    r = send(client, kim, run["id"], at=(now - timedelta(minutes=7)).isoformat())
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "at"
    assert db.execute(select(Position)).first() is None
    assert send(client, kim, run["id"], at=(now - timedelta(minutes=5, seconds=50)).isoformat()).status_code == 204


def test_what_a_position_must_be(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)

    def field(**over):
        r = send(client, kim, run["id"], **over)
        assert r.status_code == 422, r.text
        return r.json()["errors"][0]["field"]

    assert field(latitude="-90.5") == "latitude"
    assert field(longitude="180.1") == "longitude"
    assert field(at=None) == "at"
    assert field(at="2026-10-14T10:00:00") == "at"     # no time zone
    assert field(speed="80") == "speed"
    assert db.execute(select(Position)).first() is None
