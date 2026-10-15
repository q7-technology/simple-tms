"""Back at the depot: the run is done once every stop is delivered or has a
reason it wasn't."""
import uuid

from deliveries_samples import delivered, failed, on_the_road


def _finish(client, headers, run_id, message_id=None):
    return client.post(f"/v1/runs/{run_id}/finish", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4())})


def test_a_run_with_stops_left_is_not_finished(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    r = _finish(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "stops_left", "detail": f"{run['run_no']} still has 1 stop to deliver or explain"}


def test_a_run_is_finished_when_every_stop_is_done(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b)
    message_id = uuid.uuid4()
    r = _finish(client, kim, run["id"], message_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "done" and out["finished_at"] is not None
    assert (out["delivered"], out["failed"]) == (1, 1)
    assert _finish(client, kim, run["id"], message_id).json() == out
    # a new message for a finished run changes nothing either
    assert _finish(client, kim, run["id"]).json()["finished_at"] == out["finished_at"]
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["status"] == "done" and lane["finished_at"] == out["finished_at"]


def test_nothing_is_recorded_on_a_finished_run(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b)
    _finish(client, kim, run["id"])
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                    json={"message_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "run_done"
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json() == {
        "code": "run_not_planned", "detail": f"{run['run_no']} is finished; its stops can't change"}


def test_the_office_finishes_a_run_the_driver_left_open(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    r = _finish(client, dispatcher_headers, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "stops_left"
    failed(client, kim, run["id"], b)
    r = _finish(client, dispatcher_headers, run["id"])
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_finishing_takes_a_driver_or_runs_write(client, db, dispatcher_headers, capsys):
    from conftest import bearer
    from tms.cli import main
    run, _, _ = on_the_road(client, db, dispatcher_headers)
    assert main(["create-api-key", "--name", "viewer", "--scopes", "runs:read"]) == 0
    raw = capsys.readouterr().out.strip().splitlines()[-1]
    assert _finish(client, bearer(raw), run["id"]).status_code == 403


# --- what came back ----------------------------------------------------------------

def test_finishing_tells_simple_wms_what_came_back(client, db, dispatcher_headers):
    """Back at the depot, the cartons that couldn't be delivered are off the
    truck: a return call to Simple WMS waits in the queue for each, in the
    same transaction as the finish, so none is lost and none is made twice."""
    from sqlalchemy import select

    from tms.models import OutboundCall
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b, reason="closed", note="Shop shut, sign on the door")
    # not while the truck is still out
    assert [c.kind for c in db.execute(select(OutboundCall)).scalars()] == ["ship", "ship"]

    assert _finish(client, kim, run["id"]).status_code == 200
    db.expire_all()
    back = db.execute(select(OutboundCall).where(OutboundCall.kind == "return")).scalars().all()
    assert [(c.path, c.run_id, c.manifest_id, c.consignment_id, c.status) for c in back] == [
        (f"/v1/deliveries/{b.wms_ref}/return", run["id"], None, b.id, "pending")]
    assert back[0].body == {"message_id": str(back[0].message_id), "owner": "DEFAULT",
                            "reason": "closed", "note": "Shop shut, sign on the door",
                            "tracking_no": run["run_no"], "returned_by": "kim"}
    # finishing again queues nothing more
    _finish(client, dispatcher_headers, run["id"])
    assert db.execute(select(OutboundCall).where(OutboundCall.kind == "return")).scalars().all() == back

    history = client.get(f"/v1/consignments/{b.id}", headers=dispatcher_headers).json()["history"]
    assert (history[-1]["kind"], history[-1]["detail"]) == (
        "returned", {"run": run["run_no"], "run_id": run["id"], "by": "kim"})
    assert client.get(f"/v1/consignments/{b.id}", headers=dispatcher_headers).json()["status"] == "failed"
    kinds = [h["kind"] for h in client.get(f"/v1/consignments/{a.id}",
                                           headers=dispatcher_headers).json()["history"]]
    assert "returned" not in kinds


def test_a_run_that_delivered_everything_queues_no_return(client, db, dispatcher_headers):
    from sqlalchemy import select

    from tms.models import OutboundCall
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    delivered(client, kim, run["id"], b)
    _finish(client, kim, run["id"])
    assert db.execute(select(OutboundCall).where(OutboundCall.kind == "return")).first() is None


def test_the_office_finishing_says_who_brought_it_back(client, db, dispatcher_headers):
    from sqlalchemy import select

    from tms.models import OutboundCall
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    failed(client, kim, run["id"], a, reason="refused")
    failed(client, kim, run["id"], b, reason="wrong_address")
    _finish(client, dispatcher_headers, run["id"])
    back = db.execute(select(OutboundCall).where(OutboundCall.kind == "return")
                      .order_by(OutboundCall.id)).scalars().all()
    assert [(c.consignment_id, c.body["reason"], c.body["note"], c.body["returned_by"])
            for c in back] == [(a.id, "refused", None, "dee"), (b.id, "wrong_address", None, "dee")]


def test_a_run_counts_what_it_handed_back_apart_from_what_it_shipped(client, db, dispatcher_headers):
    from sqlalchemy import update

    from tms.models import OutboundCall
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["returns"] == {"pending": 0, "done": 0, "failed": 0, "last_error": None}
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b)
    _finish(client, kim, run["id"])
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["shipping"] == {"pending": 2, "done": 0, "failed": 0, "last_error": None}
    assert lane["returns"] == {"pending": 1, "done": 0, "failed": 0, "last_error": None}

    db.execute(update(OutboundCall).where(OutboundCall.kind == "return")
               .values(status="failed", attempts=5, last_error="HTTP 422: no returns zone"))
    db.commit()
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["returns"] == {"pending": 0, "done": 0, "failed": 1, "last_error": "HTTP 422: no returns zone"}
    assert lane["shipping"]["failed"] == 0
    # Try again starts both kinds again
    lane = client.post(f"/v1/runs/{run['id']}/retry-ship", headers=dispatcher_headers).json()
    assert lane["returns"] == {"pending": 1, "done": 0, "failed": 0, "last_error": None}
