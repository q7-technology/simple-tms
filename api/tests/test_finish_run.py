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
