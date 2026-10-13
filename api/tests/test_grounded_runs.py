"""Rule 3: a grounded or overdue truck does not go on a run. Refused
wherever a truck is given a run or sent out (a new run, a change of truck, a
stop added, a run sheet, leaving); a run already planned for it says so
plainly, and can be given another truck or have its stops moved."""
import uuid
from datetime import date, timedelta

import pytest

from deliveries_samples import delivered, driver_headers, on_the_road, planned_run, prestart
from fleet_samples import record
from runs_samples import DAY, assign, consignment, new_run, vehicle

GROUNDED = ("TMS041 is grounded: Brakes failed the pre-start. "
            "A fleet manager has to mark it fixed first.")


@pytest.fixture
def platen(monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "platen_url", "http://platen.test/intake/jobs")


def _grounded_run(client, db, headers):
    run, cons = planned_run(client, db, headers)
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"], failed=("Brakes",)).json()["result"] == "grounded"
    return run, cons, kim


def _refused(r, code="vehicle_grounded", detail=GROUNDED):
    assert r.status_code == 409, r.text
    assert r.json() == {"code": code, "detail": detail}


def test_a_grounded_truck_is_refused_everywhere_it_could_be_sent_out(client, db,
                                                                    dispatcher_headers, platen):
    run, _, kim = _grounded_run(client, db, dispatcher_headers)
    rid = run["id"]
    extra = consignment(db, ref="0080039001")
    _refused(assign(client, dispatcher_headers, rid, extra.id))
    _refused(client.post(f"/v1/runs/{rid}/print", headers=dispatcher_headers))
    _refused(client.post(f"/v1/runs/{rid}/depart", headers=dispatcher_headers))
    _refused(client.post(f"/v1/runs/{rid}/start", headers=kim,
                         json={"message_id": str(uuid.uuid4())}))
    tomorrow = (DAY + timedelta(days=1)).isoformat()
    _refused(client.post("/v1/runs", headers=dispatcher_headers,
                         json={"date": tomorrow, "vehicle": "TMS041", "driver": "kim"}))
    vehicle(db, rego="TMS052")
    other = new_run(client, dispatcher_headers, rego="TMS052", day=DAY + timedelta(days=1))
    _refused(client.patch(f"/v1/runs/{other['id']}", headers=dispatcher_headers,
                          json={"vehicle": "TMS041"}))


def test_a_run_planned_for_a_truck_now_grounded_says_so_and_can_be_mended(
        client, db, dispatcher_headers):
    run, (a, b), _ = _grounded_run(client, db, dispatcher_headers)
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["vehicle"]["blocked"] == {"code": "vehicle_grounded", "message": GROUNDED}
    assert lane["prestart"] is None              # the check that grounded it is no pre-start
    # its stops can still be put in order or taken off, and the driver changed
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [b.id, a.id], "seen": [a.id, b.id]})
    assert r.status_code == 200
    # or it is given another truck, and goes
    vehicle(db, rego="TMS052")
    r = client.patch(f"/v1/runs/{run['id']}", headers=dispatcher_headers,
                     json={"vehicle": "TMS052"})
    assert r.status_code == 200 and r.json()["vehicle"]["blocked"] is None
    assert r.json()["prestart"] is None          # TMS052 has had no check for it
    assert client.post(f"/v1/runs/{run['id']}/depart",
                       headers=dispatcher_headers).status_code == 200


def test_rego_overdue_on_the_runs_day_is_refused_and_the_day_before_is_not(
        client, db, dispatcher_headers, manager_headers):
    v = vehicle(db)
    from runs_samples import driver
    driver(db)
    record(db, v, "rego", expires_on=DAY)
    assert new_run(client, dispatcher_headers, day=DAY)["vehicle"]["blocked"] is None
    r = client.post("/v1/runs", headers=dispatcher_headers, json={
        "date": (DAY + timedelta(days=1)).isoformat(), "vehicle": "TMS041", "driver": "kim"})
    _refused(r, "vehicle_overdue",
             "TMS041's rego runs out on 14 Oct 2026. Record the new expiry in Fleet first.")
    r = client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                    json={"expires_on": "2027-10-14", "note": "Renewed for a year"})
    assert r.status_code == 201
    assert client.post("/v1/runs", headers=dispatcher_headers, json={
        "date": (DAY + timedelta(days=1)).isoformat(), "vehicle": "TMS041",
        "driver": "kim"}).status_code == 201


def test_the_driver_does_the_pre_start_before_starting(client, db, dispatcher_headers,
                                                       manager_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    start = {"message_id": str(uuid.uuid4())}
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start)
    _refused(r, "prestart_needed", f"Do the pre-start check of TMS041 before starting "
                                   f"{run['run_no']}.")
    check = prestart(client, kim, run["id"], failed=("Brakes",)).json()
    _refused(client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start))
    client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                json={"check_id": check["check_id"], "note": "New pads"})
    # fixed, but the check that grounded it is no pre-start: the fixed truck is checked afresh
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start)
    _refused(r, "prestart_needed", f"Do the pre-start check of TMS041 before starting "
                                   f"{run['run_no']}.")
    assert prestart(client, kim, run["id"], message_id=uuid.uuid4()).status_code == 200
    # the same start, sent again now that the fixed truck is checked, goes
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start)
    assert r.status_code == 200 and r.json()["status"] == "departed"


def test_a_truck_grounded_on_the_road_finishes_its_run(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    # a check the phone kept in a black spot arrives after the office sent it
    assert prestart(client, kim, run["id"], failed=("Lights",)).json()["grounded"] is True
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["vehicle"]["blocked"]["code"] == "vehicle_grounded"
    assert delivered(client, kim, run["id"], a).status_code == 200
    assert date.fromisoformat(lane["date"]) == DAY


def test_a_run_from_an_earlier_day_is_judged_for_today_when_it_leaves(
        client, db, dispatcher_headers):
    """Planning judges the run's own day; leaving judges today, if later: a run
    planned for last week can't leave on a rego that ran out since."""
    from tms.services import fleet
    today = fleet.today()
    v = vehicle(db)
    from runs_samples import driver
    driver(db)
    record(db, v, "rego", expires_on=today - timedelta(days=1))
    run = new_run(client, dispatcher_headers, day=today - timedelta(days=3))
    assert run["vehicle"]["blocked"] is None          # good on its own day
    c = consignment(db)
    assert assign(client, dispatcher_headers, run["id"], c.id).status_code == 200
    detail = (f"TMS041's rego runs out on {fleet.long_date(today - timedelta(days=1))}. "
              "Record the new expiry in Fleet first.")
    _refused(client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers),
             "vehicle_overdue", detail)
    kim = driver_headers(client)
    _refused(client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                         json={"message_id": str(uuid.uuid4())}), "vehicle_overdue", detail)
