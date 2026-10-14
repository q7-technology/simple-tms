"""The office sees where each truck on the road is, and when it was last
heard from: the latest position of each run on the road, and nothing from
before Start or after the run is finished."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from conftest import bearer
from deliveries_samples import delivered, driver_headers, on_the_road
from runs_samples import DAY, assign, consignment, driver, new_run, vehicle
from test_api_keys import _key
from test_positions import send
from tms.models import Position


def latest(client, headers, day=DAY):
    return client.get("/v1/positions", params={"date": day.isoformat()}, headers=headers)


def _older(db, seconds=30):
    db.execute(update(Position).values(received_at=Position.received_at - timedelta(seconds=seconds),
                                       recorded_at=Position.recorded_at - timedelta(seconds=seconds)))
    db.commit()


def test_each_truck_on_the_road_with_where_it_was_last_heard_from(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    assert send(client, kim, run["id"], latitude="-37.5", longitude="143.8").status_code == 204
    _older(db)
    assert send(client, kim, run["id"], latitude="-37.56", longitude="143.85",
                accuracy_m="8").status_code == 204
    r = latest(client, dispatcher_headers)
    assert r.status_code == 200, r.text
    (p,) = r.json()["items"]
    assert p | {"at": None, "received_at": None} == {
        "run_id": run["id"], "run_no": run["run_no"],
        "vehicle": {"rego": "TMS041", "type": "van"}, "driver": {"code": "kim", "name": "Kim"},
        "latitude": "-37.56", "longitude": "143.85", "accuracy_m": "8", "at": None,
        "received_at": None}
    assert datetime.fromisoformat(p["received_at"]) > datetime.now(UTC) - timedelta(seconds=10)


def test_a_finished_run_shows_no_position(client, db, dispatcher_headers):
    run, (a,), kim = on_the_road(client, db, dispatcher_headers, n=1)
    send(client, kim, run["id"])
    delivered(client, kim, run["id"], a)
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim, json={"message_id": str(uuid.uuid4())})
    assert latest(client, dispatcher_headers).json() == {"items": [], "total": 0}


def test_only_the_days_runs(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    send(client, kim, run["id"])
    assert latest(client, dispatcher_headers, DAY + timedelta(days=1)).json()["total"] == 0


def test_two_trucks_each_with_its_own(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    vehicle(db, rego="TMS052")
    driver(db, code="mel", name="Mel")
    other = new_run(client, dispatcher_headers, rego="TMS052", code="mel")
    assign(client, dispatcher_headers, other["id"], consignment(db, ref="0080099999").id)
    client.post(f"/v1/runs/{other['id']}/depart", headers=dispatcher_headers)
    send(client, kim, run["id"], latitude="-37.1")
    send(client, driver_headers(client, code="mel"), other["id"], latitude="-37.2")
    items = latest(client, dispatcher_headers).json()["items"]
    assert [(p["vehicle"]["rego"], p["latitude"]) for p in items] == [("TMS041", "-37.1"),
                                                                      ("TMS052", "-37.2")]


def test_who_may_see_where_the_trucks_are(client, db, dispatcher_headers, manager_headers,
                                          capsys):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    send(client, kim, run["id"])
    assert latest(client, manager_headers).status_code == 200
    # not a driver, and a system only with a key that says so
    assert latest(client, kim).status_code == 403
    assert latest(client, bearer(_key(capsys, "erp", "runs:read"))).status_code == 403
    assert latest(client, bearer(_key(capsys, "tracker", "location:read"))).status_code == 200


def test_the_map_tiles_as_set_up(client, db, dispatcher_headers, monkeypatch):
    from tms.config import get_settings

    assert client.get("/v1/map", headers=dispatcher_headers).json() == {
        "tile_url": None, "attribution": None}
    monkeypatch.setattr(get_settings(), "map_tile_url", "/tiles/{z}/{x}/{y}.png")
    monkeypatch.setattr(get_settings(), "map_attribution", "© OpenStreetMap contributors")
    assert client.get("/v1/map", headers=dispatcher_headers).json() == {
        "tile_url": "/tiles/{z}/{x}/{y}.png", "attribution": "© OpenStreetMap contributors"}
