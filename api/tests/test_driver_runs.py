"""What the driver's phone reads: today's runs, and a whole run to keep."""
from datetime import timedelta

from deliveries_samples import driver_headers, on_the_road, planned_run
from runs_samples import DAY, driver, new_run, vehicle


def test_a_driver_sees_their_own_runs_for_the_day(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    vehicle(db, rego="TMS052")
    new_run(client, dispatcher_headers, rego="TMS052", code="mel")
    kim = driver_headers(client)
    r = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=kim)
    assert r.status_code == 200, r.text
    assert r.json() == [{"id": run["id"], "run_no": run["run_no"], "date": "2026-10-14",
                         "status": "planned", "vehicle": {"rego": "TMS041", "type": "van"},
                         "stops": 2, "packages": 2, "delivered": 0, "failed": 0}]
    later = client.get("/v1/driver/runs", params={"date": "2026-10-15"}, headers=kim)
    assert later.json() == []


def test_a_run_left_unfinished_on_an_earlier_day_still_shows(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    next_day = (DAY + timedelta(days=1)).isoformat()
    r = client.get("/v1/driver/runs", params={"date": next_day}, headers=kim)
    assert [x["id"] for x in r.json()] == [run["id"]]


def test_only_a_driver_has_runs_of_their_own(client, dispatcher_headers):
    r = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=dispatcher_headers)
    assert r.status_code == 403


def test_the_whole_run_loads_onto_the_phone(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    r = client.get(f"/v1/runs/{run['id']}/stops", headers=driver_headers(client))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["run_no"] == run["run_no"] and out["status"] == "planned"
    assert out["vehicle"] == {"rego": "TMS041", "type": "van"}
    assert out["driver"] == {"code": "kim", "name": "Kim"}
    first = out["stops"][0]
    assert first == {
        "sequence": 1, "consignment_id": cons[0].id, "wms_ref": "0080034500",
        "customer": "Customer 0",
        "ship_to": {"name": "Customer 0", "address": "1 Main St", "suburb": "Ballan",
                    "state": "VIC", "postcode": "3342"},
        "packages": [{"package_no": 1, "type": "carton", "weight_kg": "100", "sscc": None}],
        "outcome": None, "outcome_at": None}


def test_a_driver_cannot_read_someone_elses_run(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    mel = driver_headers(client, code="mel")
    assert client.get(f"/v1/runs/{run['id']}/stops", headers=mel).status_code == 404
    # the office can
    assert client.get(f"/v1/runs/{run['id']}/stops", headers=dispatcher_headers).status_code == 200
    assert client.get(f"/v1/runs/{run['id']}/stops").status_code == 401
