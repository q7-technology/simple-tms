"""Couldn't deliver: the reason is kept, the cartons come back, and the
dispatcher plans the consignment again on another day."""
from datetime import timedelta

from sqlalchemy import select

from deliveries_samples import JPEG, delivered, failed, on_the_road, put_file
from runs_samples import DAY, assign, new_run, order, vehicle
from tms.models import Pod


def test_a_stop_that_couldnt_be_delivered_says_why(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    r = failed(client, kim, run["id"], b, reason="closed", note="Rang the number, no answer.",
               photo_file=str(photo))
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "failed" and r.json()["sequence"] == 2
    pod = db.execute(select(Pod)).scalar_one()
    assert (pod.reason, pod.note, pod.photo_file_id) == ("closed", "Rang the number, no answer.",
                                                         photo)
    detail = client.get(f"/v1/consignments/{b.id}", headers=dispatcher_headers).json()
    assert detail["status"] == "failed"
    assert detail["history"][-1]["kind"] == "failed"
    assert detail["history"][-1]["detail"] == {
        "run": run["run_no"], "run_id": run["id"], "pod_id": pod.id, "reason": "closed",
        "note": "Rang the number, no answer.", "photo_file": str(photo), "by": "kim"}
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["stops"][1]["outcome"] == "failed" and lane["stops"][1]["reason"] == "closed"


def test_only_the_four_reasons(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    r = failed(client, kim, run["id"], a, reason="lunch")
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "reason"
    assert failed(client, kim, run["id"], a, reason="wrong_address").status_code == 200


def test_a_failed_consignment_is_planned_again_on_another_day(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b, reason="closed")
    # it waits in the tray with the packed ones
    tray = client.get("/v1/consignments", params={"status": "packed,failed"},
                      headers=dispatcher_headers).json()
    assert [c["id"] for c in tray["items"]] == [b.id]

    vehicle(db, rego="TMS018")
    tomorrow = new_run(client, dispatcher_headers, rego="TMS018", day=DAY + timedelta(days=1))
    r = assign(client, dispatcher_headers, tomorrow["id"], b.id)
    assert r.status_code == 200, r.text
    assert order(r.json()) == [b.id] and r.json()["stops"][0]["consignment"]["status"] == "assigned"
    # today's run still shows what happened there
    today = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert [s["outcome"] for s in today["stops"]] == ["delivered", "failed"]
    kinds = [e["kind"] for e in client.get(f"/v1/consignments/{b.id}",
                                           headers=dispatcher_headers).json()["history"]]
    assert kinds == ["packed", "assigned", "departed", "failed", "assigned"]
    # and it can come off again, back to the tray
    off = client.post(f"/v1/runs/{tomorrow['id']}/unassign", headers=dispatcher_headers,
                      json={"consignment_id": b.id})
    assert off.status_code == 200 and off.json()["stops"] == []


def test_a_delivered_consignment_is_not_planned_again(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    vehicle(db, rego="TMS018")
    other = new_run(client, dispatcher_headers, rego="TMS018", day=DAY + timedelta(days=1))
    r = assign(client, dispatcher_headers, other["id"], a.id)
    assert r.status_code == 409 and r.json()["code"] == "not_plannable"
