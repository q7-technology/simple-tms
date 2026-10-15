"""A consignment's calls to Simple WMS go in order, so an earlier call that
gave up holds the later ones. A run's or a manifest's shipping says which
call holds its own, and its Try again starts that call again too."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from carriers_samples import book, new_carrier
from deliveries_samples import delivered, failed, on_the_road
from runs_samples import assign, new_run, vehicle
from tms.models import OutboundCall

WHY = "HTTP 422: BAL-WH01 has no returns zone"


def _back_and_stuck(client, db, headers):
    """R-0001 shipped a and b; b came back, and its return gave up. a's own
    ship call gave up too: that holds nothing on another run."""
    run, (a, b), kim = on_the_road(client, db, headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b, reason="closed")
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim, json={"message_id": str(uuid.uuid4())})
    db.execute(update(OutboundCall).where(OutboundCall.consignment_id == b.id,
                                          OutboundCall.kind == "ship")
               .values(status="done", attempts=1))
    db.execute(update(OutboundCall).where(OutboundCall.kind == "return")
               .values(status="failed", attempts=5, last_error=WHY))
    db.execute(update(OutboundCall).where(OutboundCall.consignment_id == a.id)
               .values(status="failed", attempts=5, last_error="HTTP 503: down"))
    db.commit()
    return run, a, b


def _calls(db, consignment_id) -> list[OutboundCall]:
    db.expire_all()
    return list(db.execute(select(OutboundCall).where(OutboundCall.consignment_id == consignment_id)
                           .order_by(OutboundCall.id)).scalars())


def _again_on_a_run(client, db, headers, b) -> dict:
    vehicle(db, rego="TMS018")
    later = new_run(client, headers, rego="TMS018")
    assert assign(client, headers, later["id"], b.id).status_code == 200
    assert client.post(f"/v1/runs/{later['id']}/depart", headers=headers).status_code == 200
    return later


def test_a_run_says_which_earlier_call_holds_its_calls(client, db, dispatcher_headers):
    run, a, b = _back_and_stuck(client, db, dispatcher_headers)
    later = _again_on_a_run(client, db, dispatcher_headers, b)
    lane = client.get(f"/v1/runs/{later['id']}", headers=dispatcher_headers).json()
    assert lane["shipping"] == {
        "pending": 1, "done": 0, "failed": 0, "last_error": None, "held": 1,
        "held_by": [{"kind": "return", "run_no": run["run_no"], "manifest_no": None,
                     "wms_ref": b.wms_ref, "status": "failed", "last_error": WHY}]}
    # the run the return belongs to holds nothing of its own up
    first = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert (first["returns"]["held"], first["returns"]["held_by"]) == (0, [])
    assert (first["shipping"]["held"], first["shipping"]["held_by"]) == (0, [])


def test_a_hold_still_being_tried_says_so(client, db, dispatcher_headers):
    run, a, b = _back_and_stuck(client, db, dispatcher_headers)
    db.execute(update(OutboundCall).where(OutboundCall.kind == "return")
               .values(status="pending", attempts=2, last_error="HTTP 503: down"))
    db.commit()
    later = _again_on_a_run(client, db, dispatcher_headers, b)
    shipping = client.get(f"/v1/runs/{later['id']}", headers=dispatcher_headers).json()["shipping"]
    assert shipping["held"] == 1
    assert [(h["status"], h["last_error"]) for h in shipping["held_by"]] == [("pending", "HTTP 503: down")]


def test_try_again_on_a_run_starts_the_failed_call_that_holds_it(client, db, dispatcher_headers):
    run, a, b = _back_and_stuck(client, db, dispatcher_headers)
    later = _again_on_a_run(client, db, dispatcher_headers, b)
    before = _calls(db, b.id)
    r = client.post(f"/v1/runs/{later['id']}/retry-ship", headers=dispatcher_headers)
    assert r.status_code == 200
    assert r.json()["shipping"]["held_by"][0]["status"] == "pending"
    after = _calls(db, b.id)
    assert [(c.kind, c.status, c.attempts, c.last_error) for c in after] == [
        ("ship", "done", 1, None), ("return", "pending", 0, None), ("ship", "pending", 0, None)]
    assert [c.message_id for c in after] == [c.message_id for c in before]
    assert after[1].next_attempt_at <= datetime.now(UTC) + timedelta(seconds=1)
    # a's call on the first run holds nothing on this one: left for its own Try again
    assert [(c.status, c.attempts) for c in _calls(db, a.id)] == [("failed", 5)]


def test_try_again_never_touches_an_earlier_call_still_being_tried(client, db, dispatcher_headers):
    run, a, b = _back_and_stuck(client, db, dispatcher_headers)
    db.execute(update(OutboundCall).where(OutboundCall.kind == "return")
               .values(status="pending", attempts=3, last_error="HTTP 503: down"))
    db.commit()
    later = _again_on_a_run(client, db, dispatcher_headers, b)
    client.post(f"/v1/runs/{later['id']}/retry-ship", headers=dispatcher_headers)
    back = _calls(db, b.id)[1]
    assert (back.status, back.attempts, back.last_error) == ("pending", 3, "HTTP 503: down")


def test_a_manifest_says_which_call_holds_it_and_its_try_again_starts_it(
        client, db, manager_headers, dispatcher_headers):
    run, a, b = _back_and_stuck(client, db, dispatcher_headers)
    rx = new_carrier(client, manager_headers)
    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042").json()
    r = client.post(f"/v1/manifests/{m['id']}/collect", headers=dispatcher_headers,
                    json={"message_id": str(uuid.uuid4()), "consignment_ids": [b.id]})
    assert r.status_code == 200
    assert r.json()["shipping"] == {
        "pending": 1, "done": 0, "failed": 0, "last_error": None, "held": 1,
        "held_by": [{"kind": "return", "run_no": run["run_no"], "manifest_no": None,
                     "wms_ref": b.wms_ref, "status": "failed", "last_error": WHY}]}
    r = client.post(f"/v1/manifests/{m['id']}/retry-ship", headers=dispatcher_headers)
    assert r.status_code == 200
    assert [(c.kind, c.status, c.attempts) for c in _calls(db, b.id)] == [
        ("ship", "done", 1), ("return", "pending", 0), ("ship", "pending", 0)]
    assert [(c.status, c.attempts) for c in _calls(db, a.id)] == [("failed", 5)]
