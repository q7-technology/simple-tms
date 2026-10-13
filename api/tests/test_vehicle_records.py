"""What a fleet manager records about a truck: a new rego expiry, a service,
an odometer put right. Each a new record with a note; the history keeps all."""
from datetime import date

from fleet_samples import check
from runs_samples import vehicle


def _vehicle(client, headers, day="2026-10-14"):
    return client.get("/v1/vehicles/TMS041", headers=headers, params={"date": day}).json()


def test_a_fleet_manager_records_the_new_rego_expiry(client, db, manager_headers,
                                                     dispatcher_headers):
    vehicle(db)
    body = {"expires_on": "2027-03-03", "note": "Renewed online, receipt 4471"}
    assert client.post("/v1/vehicles/TMS041/rego", headers=dispatcher_headers,
                       json=body).status_code == 403
    r = client.post("/v1/vehicles/tms041/rego", headers=manager_headers, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert {k: out[k] for k in ("vehicle", "kind", "expires_on", "note", "recorded_by")} == {
        "vehicle": "TMS041", "kind": "rego", "expires_on": "2027-03-03",
        "note": "Renewed online, receipt 4471", "recorded_by": "fleet.mgr"}
    assert _vehicle(client, manager_headers)["rego_due"]["expires_on"] == "2027-03-03"
    # a note is needed: a person clears it with a note (rule 3)
    r = client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                    json={"expires_on": "2027-03-03", "note": "  "})
    assert r.status_code == 422
    assert client.post("/v1/vehicles/NOPE/rego", headers=manager_headers,
                       json=body).status_code == 404


def test_a_service_and_an_odometer_put_right_are_kept_with_the_checks(client, db,
                                                                       manager_headers):
    v = vehicle(db)
    client.patch("/v1/vehicles/TMS041", headers=manager_headers, json={"service_every_km": 15000})
    check(db, v, km=61208)
    r = client.post("/v1/vehicles/TMS041/service", headers=manager_headers,
                    json={"done_on": "2026-10-13", "odometer_km": 61190,
                          "note": "60,000 km service at Ballarat Diesel"})
    assert r.status_code == 201, r.text
    assert _vehicle(client, manager_headers)["service_due"]["due_km"] == 76190
    r = client.post("/v1/vehicles/TMS041/odometer", headers=manager_headers,
                    json={"odometer_km": 61250, "note": "Read it off the dash"})
    assert r.status_code == 201 and _vehicle(client, manager_headers)["odometer_km"] == 61250
    client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                json={"expires_on": "2026-12-01", "note": "Typo"})
    client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                json={"expires_on": "2027-12-01", "note": "The right year"})
    history = client.get("/v1/vehicles/TMS041/history", headers=manager_headers).json()
    assert [h["kind"] for h in history] == ["rego", "rego", "odometer", "service", "prestart"]
    assert [h.get("expires_on") for h in history[:2]] == ["2027-12-01", "2026-12-01"]
    assert history[4] | {"recorded_at": None} == {
        "kind": "prestart", "id": 1, "recorded_at": None, "by": "tom", "note": None,
        "result": "passed", "failed": [], "odometer_km": 61208, "done_on": None,
        "expires_on": None, "check_id": None}
    assert _vehicle(client, manager_headers)["rego_due"]["expires_on"] == "2027-12-01"
    assert date.fromisoformat(history[3]["done_on"]) == date(2026, 10, 13)


def test_a_service_is_recorded_once_it_is_done(client, db, manager_headers):
    """Not before: a date days ahead is a slip that would put the next service
    off. Tomorrow is taken, as the server's day (UTC) is behind Australia's."""
    from datetime import timedelta

    from tms.services import fleet
    vehicle(db)
    ahead = fleet.today() + timedelta(days=2)
    r = client.post("/v1/vehicles/TMS041/service", headers=manager_headers,
                    json={"done_on": ahead.isoformat(), "note": "Booked in"})
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "done_on",
        "message": f"{fleet.long_date(ahead)} hasn't come yet: record a service once it is done"}]
    r = client.post("/v1/vehicles/TMS041/service", headers=manager_headers,
                    json={"done_on": (fleet.today() + timedelta(days=1)).isoformat(),
                          "note": "Done this morning"})
    assert r.status_code == 201


def test_only_a_fleet_manager_records_rego_service_or_odometer(client, db, dispatcher_headers):
    from deliveries_samples import driver_headers
    from runs_samples import driver
    v = vehicle(db)
    driver(db)
    kim = driver_headers(client)
    bodies = {"rego": {"expires_on": "2027-03-03", "note": "Renewed"},
              "service": {"done_on": "2026-10-01", "odometer_km": 61000, "note": "Serviced"},
              "odometer": {"odometer_km": 61250, "note": "Off the dash"}}
    for who in (dispatcher_headers, kim):
        for kind, body in bodies.items():
            r = client.post(f"/v1/vehicles/TMS041/{kind}", headers=who, json=body)
            assert r.status_code == 403, (kind, r.text)
    from sqlalchemy import select

    from tms.models import VehicleRecord
    assert db.execute(select(VehicleRecord).where(VehicleRecord.vehicle_id == v.id)).first() is None
