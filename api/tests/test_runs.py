"""Making runs: a truck and a driver for a day."""
from runs_samples import DAY, driver, new_run, vehicle


def test_a_dispatcher_makes_a_run_for_a_day(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    assert run["run_no"] == f"R-{run['id']:04d}"
    assert run["date"] == "2026-10-14" and run["status"] == "planned"
    assert run["vehicle"] == {"rego": "TMS041", "type": "van", "max_kg": "1200", "max_m3": "9",
                              "active": True}
    assert run["driver"] == {"code": "kim", "name": "Kim"}
    assert run["created_by"] == "dee"
    assert run["stops"] == []
    assert run["load"] == {"weight_kg": "0", "volume_m3": "0", "max_kg": "1200", "max_m3": "9",
                           "unknown_weight": 0, "unknown_volume": 0, "state": "ok",
                           "message": None}
    assert run["run_sheet"] is None
    assert run["shipping"] == {"pending": 0, "done": 0, "failed": 0}


def test_the_rego_is_read_however_it_is_typed(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    assert new_run(client, dispatcher_headers, rego="tms 041")["vehicle"]["rego"] == "TMS041"


def test_a_day_lists_its_runs(client, db, dispatcher_headers):
    vehicle(db)
    vehicle(db, rego="TMS018")
    driver(db)
    a = new_run(client, dispatcher_headers)
    b = new_run(client, dispatcher_headers, rego="TMS018")
    r = client.get("/v1/runs", params={"date": DAY.isoformat()}, headers=dispatcher_headers)
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["items"]] == [a["id"], b["id"]]
    other = client.get("/v1/runs", params={"date": "2026-10-15"}, headers=dispatcher_headers)
    assert other.json()["total"] == 0
    assert client.get("/v1/runs", headers=dispatcher_headers).status_code == 422


def test_a_truck_takes_one_run_a_day(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    driver(db, code="raj", name="Raj")
    first = new_run(client, dispatcher_headers)
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "TMS041", "driver": "raj"})
    assert r.status_code == 422
    taken = f"TMS041 already has {first['run_no']} on 2026-10-14"
    assert r.json()["errors"] == [{"field": "vehicle", "message": taken}]
    tomorrow = client.post("/v1/runs", headers=dispatcher_headers,
                           json={"date": "2026-10-15", "vehicle": "TMS041", "driver": "raj"})
    assert tomorrow.status_code == 201


def test_a_deactivated_truck_or_driver_cannot_take_a_run(client, db, dispatcher_headers):
    vehicle(db, active=False)
    vehicle(db, rego="TMS018")
    d = driver(db)
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "TMS041", "driver": "kim"})
    assert r.status_code == 422
    assert r.json()["errors"][0] == {"field": "vehicle",
                                     "message": "TMS041 is deactivated, so it can't take a run"}
    d.active = False
    db.commit()
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "TMS018", "driver": "kim"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "driver"


def test_an_unknown_truck_or_driver_is_named(client, db, dispatcher_headers):
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "NOPE", "driver": "kim"})
    assert r.status_code == 422 and r.json()["errors"][0]["message"] == "no vehicle NOPE"


def test_a_planned_run_changes_truck_or_driver(client, db, dispatcher_headers):
    vehicle(db)
    vehicle(db, rego="TMS052", type="rigid", max_kg="4500", max_m3="22")
    driver(db)
    driver(db, code="mel", name="Mel")
    run = new_run(client, dispatcher_headers)
    r = client.patch(f"/v1/runs/{run['id']}", headers=dispatcher_headers,
                     json={"vehicle": "TMS052", "driver": "mel"})
    assert r.status_code == 200, r.text
    assert r.json()["vehicle"]["rego"] == "TMS052" and r.json()["driver"]["code"] == "mel"
    assert r.json()["load"]["max_kg"] == "4500"


def test_an_empty_run_is_cancelled_and_frees_the_truck(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    listed = client.get("/v1/runs", params={"date": DAY.isoformat()}, headers=dispatcher_headers)
    assert listed.json()["total"] == 0
    with_cancelled = client.get("/v1/runs", params={"date": DAY.isoformat(), "status": "cancelled"},
                                headers=dispatcher_headers)
    assert with_cancelled.json()["total"] == 1
    new_run(client, dispatcher_headers)     # the truck is free again
    again = client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers)
    assert again.status_code == 200 and again.json()["status"] == "cancelled"


def test_a_driver_cannot_plan_and_nobody_without_a_session_can_look(client, db):
    from conftest import bearer
    driver(db)
    signed_in = client.post("/v1/auth/driver-login", json={"code": "kim", "pin": "2468"})
    token = signed_in.json()["token"]
    day = {"date": DAY.isoformat()}
    assert client.get("/v1/runs", params=day, headers=bearer(token)).status_code == 403
    assert client.get("/v1/runs", params=day).status_code == 401


def test_the_driver_is_read_however_the_code_is_typed(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    assert new_run(client, dispatcher_headers, code="KIM")["driver"]["code"] == "kim"
