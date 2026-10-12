"""A grounded truck stays grounded until a fleet manager marks it fixed, with
a note: a new record, never a change to the failed check."""
import uuid

from deliveries_samples import driver_headers, planned_run, prestart
from runs_samples import DAY


def _grounded(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], failed=("Lights", "Mirrors"))
    assert r.status_code == 200 and r.json()["result"] == "grounded"
    return run, kim, r.json()["check_id"]


def _state(client, headers):
    return client.get("/v1/vehicles/TMS041", headers=headers,
                      params={"date": DAY.isoformat()}).json()["state"]


def test_the_office_sees_the_failed_check_with_its_photos_and_notes(client, db,
                                                                   dispatcher_headers):
    run, _, check_id = _grounded(client, db, dispatcher_headers)
    r = client.get(f"/v1/prestarts/{check_id}", headers=dispatcher_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert {k: out[k] for k in ("run_no", "vehicle", "vehicle_type", "driver", "odometer_km",
                                "result", "fixed")} == {
        "run_no": run["run_no"], "vehicle": "TMS041", "vehicle_type": "van",
        "driver": {"code": "kim", "name": "Kim"}, "odometer_km": 61208, "result": "grounded",
        "fixed": None}
    lights = out["items"][1]
    assert (lights["label"], lights["critical"], lights["passed"], lights["note"]) == (
        "Lights", True, False, "Lights not right")
    assert out["items"][0] == {"label": "Tyres", "critical": False, "passed": True, "note": None,
                               "photo_file": None}
    photo = client.get(f"/v1/pod-files/{lights['photo_file']}", headers=dispatcher_headers)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert client.get("/v1/prestarts/999", headers=dispatcher_headers).status_code == 404


def test_a_fleet_manager_marks_it_fixed_with_a_note(client, db, dispatcher_headers,
                                                    manager_headers):
    _, _, check_id = _grounded(client, db, dispatcher_headers)
    body = {"check_id": check_id, "note": "Replaced left brake globe, tested"}
    assert client.post("/v1/vehicles/TMS041/fix", headers=dispatcher_headers,
                       json=body).status_code == 403
    assert client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                       json={"check_id": check_id, "note": ""}).status_code == 422
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers, json=body)
    assert r.status_code == 201, r.text
    assert (r.json()["kind"], r.json()["check_id"], r.json()["recorded_by"]) == (
        "fixed", check_id, "fleet.mgr")
    assert _state(client, dispatcher_headers) == "ok"
    out = client.get(f"/v1/prestarts/{check_id}", headers=dispatcher_headers).json()
    assert out["result"] == "grounded"                  # the check is as it was sent
    assert out["fixed"]["note"] == "Replaced left brake globe, tested"
    assert out["fixed"]["by"] == "fleet.mgr"
    history = client.get("/v1/vehicles/TMS041/history", headers=dispatcher_headers).json()
    assert [h["kind"] for h in history] == ["fixed", "prestart"]
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers, json=body)
    assert r.status_code == 409 and r.json() == {"code": "not_grounded",
                                                 "detail": "TMS041 isn't grounded"}


def test_a_failed_check_that_came_in_meanwhile_is_not_cleared_unseen(client, db,
                                                                    dispatcher_headers,
                                                                    manager_headers):
    run, kim, first = _grounded(client, db, dispatcher_headers)
    second = prestart(client, kim, run["id"], failed=("Brakes",), message_id=uuid.uuid4())
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                    json={"check_id": first, "note": "New globe"})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert _state(client, dispatcher_headers) == "grounded"
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                    json={"check_id": second.json()["check_id"], "note": "Globe and brake pads"})
    assert r.status_code == 201 and _state(client, dispatcher_headers) == "ok"
    # the one fix clears both: each check names it
    out = client.get(f"/v1/prestarts/{first}", headers=dispatcher_headers).json()
    assert out["fixed"]["note"] == "Globe and brake pads"
