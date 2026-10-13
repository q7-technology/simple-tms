"""The driver's pre-start check, from the phone: every item of the truck's
checklist passed or failed, a failed one with a photo and a note, and the
odometer. A failed critical item grounds the truck at once. Kept as sent,
once per message_id."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import (
    JPEG, VAN_ITEMS, delivered, driver_headers, on_the_road, planned_run, prestart, put_file,
)
from runs_samples import DAY, driver, vehicle
from tms.models import InboundMessage, PrestartCheck, Vehicle


def _vehicle(client, headers):
    return client.get("/v1/vehicles/TMS041", headers=headers,
                      params={"date": DAY.isoformat()}).json()


def _my_run(client, kim, run_id):
    runs = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=kim).json()
    return next(r for r in runs if r["id"] == run_id)


def test_a_passed_check_is_kept_once_with_the_odometer(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    mine = _my_run(client, kim, run["id"])
    assert mine["prestart"] is None
    assert mine["checklist"][1] == {"id": 2, "label": "Lights", "critical": True}
    message_id = uuid.uuid4()
    r = prestart(client, kim, run["id"], km=61208, message_id=message_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert {k: out[k] for k in ("message_id", "run_no", "vehicle", "result", "failed",
                                "grounded", "odometer_km")} == {
        "message_id": str(message_id), "run_no": run["run_no"], "vehicle": "TMS041",
        "result": "passed", "failed": [], "grounded": False, "odometer_km": 61208}
    # the same message again: the same reply, nothing more kept
    again = prestart(client, kim, run["id"], km=61208, message_id=message_id)
    assert again.status_code == 200 and again.json() == out
    check = db.execute(select(PrestartCheck)).scalar_one()
    assert [(i.label, i.passed) for i in check.items][:2] == [("Tyres", True), ("Lights", True)]
    assert check.recorded_by == "kim" and check.driver_id is not None
    assert db.execute(select(InboundMessage)).scalar_one().duplicates == 1
    mine = _my_run(client, kim, run["id"])
    assert mine["prestart"]["id"] == out["check_id"] and mine["prestart"]["result"] == "passed"
    assert mine["vehicle"]["odometer_km"] == 61208


def test_a_failed_item_needs_a_photo_and_a_note_and_the_truck_may_still_go(
        client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    answers = [{"item_id": i, "result": "pass"} for i in range(1, 7)]
    answers[4] = {"item_id": 5, "result": "fail", "note": "Rear strap frayed"}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json={
        "message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00+11:00",
        "vehicle": "TMS041", "odometer_km": 61208, "items": answers})
    assert r.status_code == 422
    assert "a failed item needs a photo and a note" in r.text
    r = prestart(client, kim, run["id"], failed=("Load restraints",))
    assert r.status_code == 200, r.text
    assert (r.json()["result"], r.json()["failed"], r.json()["grounded"]) == (
        "failed", ["Load restraints"], False)
    item = db.execute(select(PrestartCheck)).scalar_one().items[4]
    assert (item.label, item.passed, item.note) == ("Load restraints", False,
                                                    "Load restraints not right")
    assert item.photo_file_id is not None
    assert _vehicle(client, dispatcher_headers)["state"] == "ok"


def test_a_failed_critical_item_grounds_the_truck_there_and_then(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], failed=("Brakes",))
    assert r.status_code == 200, r.text
    assert (r.json()["result"], r.json()["grounded"]) == ("grounded", True)
    v = _vehicle(client, dispatcher_headers)
    assert v["state"] == "grounded" and v["grounded"]["failed"] == ["Brakes"]
    assert _my_run(client, kim, run["id"])["vehicle"]["blocked"]["code"] == "vehicle_grounded"


def test_the_odometer_never_goes_back(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"], km=61208).status_code == 200
    r = prestart(client, kim, run["id"], km=61200)
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "odometer_km", "message": "61,200 km is less than TMS041's last reading, "
                                           "61,208 km. Check the odometer and try again."}]
    assert prestart(client, kim, run["id"], km=61208).status_code == 200


def test_each_item_answered_once_from_the_list_as_it_is(client, db, dispatcher_headers,
                                                       manager_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], items=[(1, "Tyres"), (2, "Lights")])
    assert r.status_code == 409 and r.json()["code"] == "checklist_changed"
    # the fleet manager adds a line while the phone has the old list
    client.put("/v1/checklists/van", headers=manager_headers, json={"items": [
        {"label": "Tyres"}, {"label": "Lights", "critical": True},
        {"label": "Brakes", "critical": True}, {"label": "Mirrors"},
        {"label": "Load restraints"}, {"label": "Damage"}, {"label": "Tail lift"}]})
    r = prestart(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "checklist_changed",
        "detail": "The van checklist has changed since this check began. Do the check again."}
    assert prestart(client, kim, run["id"], items=[*VAN_ITEMS, (19, "Tail lift")]).status_code == 200


def test_a_photo_is_the_drivers_own_and_goes_with_one_thing(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    assert delivered(client, kim, run["id"], a, photo_file=str(photo)).status_code == 200
    answers = [{"item_id": i, "result": "pass"} for i in range(1, 7)]
    answers[0] = {"item_id": 1, "result": "fail", "note": "Bald", "photo_file": str(photo)}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json={
        "message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00Z", "vehicle": "TMS041",
        "odometer_km": 1, "items": answers})
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "items.0.photo_file", "message": f"photo {photo} already belongs to another stop"}]


def test_a_photo_error_names_the_item_it_is_on(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    answers = [{"item_id": i, "result": "pass"} for i in range(1, 7)]
    answers[3] = {"item_id": 4, "result": "fail", "note": "Cracked", "photo_file": str(photo)}
    answers[5] = {"item_id": 6, "result": "fail", "note": "Dented", "photo_file": str(photo)}
    body = {"message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00Z", "vehicle": "TMS041",
            "odometer_km": 1, "items": answers}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json=body)
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "items.5.photo_file", "message": "each failed item needs a photo of its own"}]
    never_sent = uuid.uuid4()
    answers[5] = {"item_id": 6, "result": "fail", "note": "Dented", "photo_file": str(never_sent)}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim,
                    json={**body, "message_id": str(uuid.uuid4())})
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "items.5.photo_file", "message": f"no photo {never_sent} has been sent"}]


def test_only_the_runs_driver_and_only_until_it_is_finished(client, db, dispatcher_headers):
    run, (a,), kim = on_the_road(client, db, dispatcher_headers, n=1)
    driver(db, code="mel", name="Mel")
    assert prestart(client, driver_headers(client, code="mel"), run["id"]).status_code == 404
    assert prestart(client, dispatcher_headers, run["id"]).status_code == 403
    # on the road it is still taken: the check was done, so it is kept
    assert prestart(client, kim, run["id"]).status_code == 200
    delivered(client, kim, run["id"], a)
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim,
                json={"message_id": str(uuid.uuid4())})
    r = prestart(client, kim, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "run_done"


def test_two_copies_at_once_keep_one_check(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = uuid.uuid4()

    def send():
        with fresh_client() as c:
            return prestart(c, kim, run["id"], message_id=message_id).status_code

    assert all_at_once(3, send) == [200, 200, 200]
    assert len(db.execute(select(PrestartCheck)).scalars().all()) == 1


def _swap(client, headers, run, rego="TMS052", type="rigid"):
    """The office gives the run another truck while the driver checks the first."""
    r = client.patch(f"/v1/runs/{run['id']}", headers=headers, json={"vehicle": rego})
    assert r.status_code == 200, r.text


def test_a_check_is_of_the_truck_the_driver_was_shown_even_after_a_swap(
        client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    vehicle(db, rego="TMS052", type="rigid")
    kim = driver_headers(client)
    _swap(client, dispatcher_headers, run)
    message_id = uuid.uuid4()
    # the phone still showed TMS041 (a van: its list, not the rigid's) and its brakes failed
    r = prestart(client, kim, run["id"], failed=("Brakes",), km=61300, message_id=message_id)
    assert r.status_code == 409, r.text
    out = r.json()
    assert out["code"] == "vehicle_changed" and out["vehicle"] == "TMS052"
    assert out["detail"] == (f"{run['run_no']} has been given TMS052 since this check began. "
                             "Your check of TMS041 is with the office. "
                             "Do a fresh check of TMS052 before starting.")
    assert (out["check"]["vehicle"], out["check"]["result"], out["check"]["grounded"]) == (
        "TMS041", "grounded", True)
    # kept once, against the truck that was inspected: that one is grounded, the new one not
    check = db.execute(select(PrestartCheck)).scalar_one()
    assert db.get(Vehicle, check.vehicle_id).rego == "TMS041" and check.odometer_km == 61300
    old = client.get("/v1/vehicles/TMS041", headers=dispatcher_headers).json()
    new = client.get("/v1/vehicles/TMS052", headers=dispatcher_headers).json()
    assert old["state"] == "grounded" and old["odometer_km"] == 61300
    assert new["state"] != "grounded" and new["odometer_km"] is None
    # the same message again: the same answer, nothing more kept
    again = prestart(client, kim, run["id"], failed=("Brakes",), km=61300, message_id=message_id)
    assert (again.status_code, again.json()) == (409, out)
    assert len(db.execute(select(PrestartCheck)).scalars().all()) == 1
    # the run's truck has had no check, so Start still asks for one
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                    json={"message_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "prestart_needed"
    assert _my_run(client, kim, run["id"])["vehicle"]["rego"] == "TMS052"


def test_a_passed_check_of_the_old_truck_does_not_count_for_the_new_one(
        client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    vehicle(db, rego="TMS052")
    kim = driver_headers(client)
    _swap(client, dispatcher_headers, run)
    r = prestart(client, kim, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "vehicle_changed"
    assert _my_run(client, kim, run["id"])["prestart"] is None
    start = {"message_id": str(uuid.uuid4())}
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start)
    assert r.status_code == 409 and r.json()["code"] == "prestart_needed"
    # a fresh check of the truck the run has now counts, and the run starts
    r = prestart(client, kim, run["id"], vehicle="TMS052", km=12000)
    assert r.status_code == 200 and r.json()["vehicle"] == "TMS052"
    assert client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start).status_code == 200


def test_a_check_names_a_truck_that_exists(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], vehicle="NOPE99")
    assert r.status_code == 422 and r.json()["errors"] == [
        {"field": "vehicle", "message": "no vehicle NOPE99"}]
    body = {"message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00Z", "odometer_km": 1,
            "items": [{"item_id": i, "result": "pass"} for i in range(1, 7)]}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json=body)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "vehicle"
    assert db.execute(select(PrestartCheck)).first() is None


def _failed_tyres(photo) -> list[dict]:
    answers = [{"item_id": i, "result": "pass"} for i in range(1, 7)]
    answers[0] = {"item_id": 1, "result": "fail", "note": "Bald", "photo_file": str(photo)}
    return answers


def test_a_photo_a_check_has_taken_cannot_go_with_a_delivery(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json={
        "message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00Z", "vehicle": "TMS041",
        "odometer_km": 1, "items": _failed_tyres(photo)})
    assert r.status_code == 200, r.text
    r = delivered(client, kim, run["id"], a, photo_file=str(photo))
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "photo_file", "message": f"photo {photo} already belongs to a pre-start check"}]


def test_a_check_and_a_delivery_racing_for_one_photo_take_turns(client, db, engine,
                                                                dispatcher_headers):
    """The pre-start holds the photo's row until it is kept; the delivery waits,
    then finds the photo taken, rather than both claiming it."""
    from types import SimpleNamespace

    from sqlalchemy.orm import Session

    from conftest import blocked_on_a_lock, in_background
    from tms.models import Driver
    from tms.services import records
    from runs_samples import new_run
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    # Kim's second run of the day, on another truck: the check is of that one
    vehicle(db, rego="TMS052")
    second = new_run(client, dispatcher_headers, rego="TMS052")
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    d = db.execute(select(Driver).where(Driver.code == "kim")).scalar_one()
    who = SimpleNamespace(kind="driver", id=d.id, name="kim")
    with Session(engine) as other:
        records.prestart(other, who, second["id"], rego="TMS052", message_id=uuid.uuid4(),
                         at=datetime.now(UTC), odometer_km=1,
                         answers=[{"note": None, "photo_file": None, **x, "photo_file": photo}
                                  if x["result"] == "fail" else {"note": None, "photo_file": None, **x}
                                  for x in _failed_tyres(photo)])     # not committed yet

        def deliver_now():
            with fresh_client() as c:
                return delivered(c, kim, run["id"], a, photo_file=str(photo))

        delivering = in_background(deliver_now)
        assert blocked_on_a_lock(engine, delivering), "the delivery did not wait for the photo"
        other.commit()
    r = delivering.result(timeout=10)
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["field"] == "photo_file"


def test_a_check_waits_for_the_runs_day(client, db, dispatcher_headers):
    """A check is of the truck as it is on the day: one for a run days off is
    refused. The day before (in UTC) is taken, since an Australian morning is
    still yesterday on the server's clock."""
    from datetime import timedelta

    from runs_samples import new_run
    from tms.services import fleet
    vehicle(db)
    driver(db)
    later = new_run(client, dispatcher_headers, day=fleet.today() + timedelta(days=2))
    kim = driver_headers(client)
    r = prestart(client, kim, later["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "too_early",
        "detail": f"{later['run_no']} is for {fleet.long_date(fleet.today() + timedelta(days=2))}. "
                  "Do the pre-start on the day."}
    assert db.execute(select(PrestartCheck)).first() is None
    vehicle(db, rego="TMS052")
    soon = new_run(client, dispatcher_headers, rego="TMS052", day=fleet.today() + timedelta(days=1))
    assert prestart(client, kim, soon["id"], vehicle="TMS052").status_code == 200
