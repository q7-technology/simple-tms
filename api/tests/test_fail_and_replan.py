"""Couldn't deliver: the reason is kept, the cartons come back, and the
dispatcher plans the consignment again on another day."""
from sqlalchemy import select

from deliveries_samples import JPEG, failed, on_the_road, put_file
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
