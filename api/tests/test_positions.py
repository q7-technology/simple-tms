"""The driver's phone says where the truck is, about every 30 seconds, from
Start until the run is finished, and never outside a run."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from conftest import bearer
from deliveries_samples import delivered, driver_headers, on_the_road, planned_run
from runs_samples import driver
from test_api_keys import _key
from tms.models import ConsignmentEvent, Position


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
