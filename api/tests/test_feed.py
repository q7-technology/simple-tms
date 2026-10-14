"""What just happened: the day as the office hears it, newest first, from the
records already kept (nothing new is written for it)."""
import uuid

from deliveries_samples import delivered, driver_headers, failed, on_the_road, prestart, planned_run
from fleet_samples import record


def feed(client, headers, **params):
    r = client.get("/v1/feed", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_a_run_from_packed_to_back_at_the_depot(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b, reason="closed")
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim, json={"message_id": str(uuid.uuid4())})
    items = feed(client, dispatcher_headers)
    assert [i["kind"] for i in items] == ["finished", "failed", "delivered", "left", "packed",
                                          "packed"]
    done, couldnt, dropped, left, packed = items[:5]
    assert (done["run_no"], done["driver"], done["rego"]) == (run["run_no"], "Kim", "TMS041")
    assert (couldnt["sequence"], couldnt["customer"], couldnt["reason"]) == (2, "Customer 1",
                                                                             "closed")
    assert (dropped["sequence"], dropped["customer"], dropped["driver"]) == (1, "Customer 0", "Kim")
    assert (left["run_no"], left["driver"], left["rego"]) == (run["run_no"], "Kim", "TMS041")
    assert (packed["wms_ref"], packed["customer"]) == (b.wms_ref, "Customer 1")
    assert all(i["at"] for i in items)


def test_pre_starts_a_grounding_and_marked_fixed(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"]).status_code == 200
    grounded = prestart(client, kim, run["id"], failed=("Brakes",)).json()
    from tms.models import Vehicle
    record(db, db.get(Vehicle, 1), "fixed", note="New pads", check_id=grounded["check_id"])
    fixed, bad, good = feed(client, dispatcher_headers)[:3]
    assert (fixed["kind"], fixed["rego"], fixed["by"], fixed["note"]) == (
        "fixed", "TMS041", "fleet.mgr", "New pads")
    assert (bad["kind"], bad["result"], bad["failed"], bad["driver"]) == (
        "prestart", "grounded", ["Brakes"], "Kim")
    assert (good["kind"], good["result"], good["failed"], good["rego"]) == (
        "prestart", "passed", [], "TMS041")


def test_only_the_newest(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    assert [i["kind"] for i in feed(client, dispatcher_headers, limit=2)] == ["delivered", "left"]


def test_for_the_office_not_the_phone(client, db, dispatcher_headers):
    _, _, kim = on_the_road(client, db, dispatcher_headers)
    assert client.get("/v1/feed", headers=kim).status_code == 403
