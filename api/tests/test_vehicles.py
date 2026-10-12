VAN = {"rego": "1ab 2cd", "type": "van", "max_kg": "1200", "max_m3": "8.5"}


def make_vehicle(client, headers, body=VAN):
    r = client.post("/v1/vehicles", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_manager_adds_a_vehicle_with_decimal_limits(client, manager_headers):
    out = make_vehicle(client, manager_headers)
    assert out["rego"] == "1AB2CD"
    assert out["type"] == "van"
    assert out["max_kg"] == "1200" and out["max_m3"] == "8.5"
    assert out["active"] is True


def test_the_same_plate_twice_is_refused_however_it_is_typed(client, manager_headers):
    make_vehicle(client, manager_headers)
    r = client.post("/v1/vehicles", headers=manager_headers, json={**VAN, "rego": "1AB2CD"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "rego"


def test_only_van_rigid_or_ute(client, manager_headers):
    r = client.post("/v1/vehicles", headers=manager_headers, json={**VAN, "type": "bicycle"})
    assert r.status_code == 422


def test_limits_must_be_positive(client, manager_headers):
    r = client.post("/v1/vehicles", headers=manager_headers, json={**VAN, "max_kg": "0"})
    assert r.status_code == 422


def test_a_dispatcher_sees_vehicles_but_cannot_add_one(client, manager_headers, dispatcher_headers):
    make_vehicle(client, manager_headers)
    r = client.get("/v1/vehicles", headers=dispatcher_headers)
    assert r.status_code == 200 and [v["rego"] for v in r.json()["items"]] == ["1AB2CD"]
    assert client.post("/v1/vehicles", headers=dispatcher_headers, json=VAN).status_code == 403


def test_update_changes_type_and_limits(client, manager_headers):
    make_vehicle(client, manager_headers)
    r = client.patch("/v1/vehicles/1ab2cd", headers=manager_headers,
                     json={"type": "rigid", "max_kg": "8000.5"})
    assert r.status_code == 200
    assert r.json()["type"] == "rigid" and r.json()["max_kg"] == "8000.5"
    assert r.json()["max_m3"] == "8.5"


def test_a_service_schedule_is_kept_unless_cleared_with_null(client, manager_headers):
    make_vehicle(client, manager_headers, {**VAN, "service_every_months": 6,
                                           "service_every_km": 15000})
    # left out: kept
    r = client.patch("/v1/vehicles/1ab2cd", headers=manager_headers, json={"max_kg": "1300"})
    assert (r.json()["service_every_months"], r.json()["service_every_km"]) == (6, 15000)
    # null: cleared, the other kept
    r = client.patch("/v1/vehicles/1ab2cd", headers=manager_headers,
                     json={"service_every_months": None})
    assert r.status_code == 200, r.text
    assert (r.json()["service_every_months"], r.json()["service_every_km"]) == (None, 15000)
    r = client.patch("/v1/vehicles/1ab2cd", headers=manager_headers,
                     json={"service_every_km": None})
    assert (r.json()["service_every_months"], r.json()["service_every_km"]) == (None, None)
    assert r.json()["max_kg"] == "1300"


def test_deactivated_not_deleted(client, manager_headers):
    make_vehicle(client, manager_headers)
    r = client.post("/v1/vehicles/1AB2CD/deactivate", headers=manager_headers)
    assert r.json()["active"] is False
    assert client.get("/v1/vehicles?active=true", headers=manager_headers).json()["total"] == 0
    assert client.get("/v1/vehicles/1AB2CD", headers=manager_headers).status_code == 200
    r = client.post("/v1/vehicles/1AB2CD/reactivate", headers=manager_headers)
    assert r.json()["active"] is True
    assert client.delete("/v1/vehicles/1AB2CD", headers=manager_headers).status_code == 405


def test_an_unknown_vehicle_is_404(client, manager_headers):
    assert client.get("/v1/vehicles/NOPE", headers=manager_headers).status_code == 404


def test_a_plate_taken_by_another_request_meanwhile_is_refused_not_a_crash(
        client, manager_headers, engine):
    from conftest import beaten_to_it
    from tms.models import Vehicle

    def rival(obj):
        if isinstance(obj, Vehicle):
            return Vehicle(rego=obj.rego, type="ute", max_kg=1, max_m3=1, active=True)

    with beaten_to_it(engine, rival):
        r = client.post("/v1/vehicles", headers=manager_headers, json=VAN)
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "rego", "message": "vehicle 1AB2CD already exists"}]
