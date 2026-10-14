"""The carrier calls and takes its manifest: each consignment is collected,
and a ship call to Simple WMS waits in the queue for each, with the carrier's
name and the con note. Once per message_id, and only what the dispatcher saw."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from carriers_samples import RIDGELINE, book, con_note, new_carrier
from fake_http import FakeHttp
from runs_samples import DAY, consignment
from tms import worker
from tms.models import OutboundCall

T0 = datetime(2026, 10, 14, 5, 30, tzinfo=UTC)


@pytest.fixture
def bay(client, db, manager_headers, dispatcher_headers):
    """Two consignments on Ridgeline's manifest, each with its con note."""
    rx = new_carrier(client, manager_headers)
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery", kg="72")
    b = consignment(db, ref="0080034520", customer="Old Mill Books", kg="54")
    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310043").json()
    return m, a, b


def collect(client, headers, m, ids, message_id=None):
    return client.post(f"/v1/manifests/{m['id']}/collect", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4()), "consignment_ids": ids})


def _calls(db) -> list[OutboundCall]:
    db.expire_all()
    return list(db.execute(select(OutboundCall).order_by(OutboundCall.id)).scalars())


def test_collected_queues_a_ship_call_each_with_the_carrier_and_con_note(
        client, db, bay, dispatcher_headers):
    m, a, b = bay
    r = collect(client, dispatcher_headers, m, [a.id, b.id])
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["collected_by"]) == ("collected", "dee") and out["collected_at"]
    assert [line["consignment"]["status"] for line in out["lines"]] == ["collected", "collected"]
    assert out["shipping"] == {"pending": 2, "done": 0, "failed": 0}
    calls = _calls(db)
    assert [(c.path, c.manifest_id, c.run_id, c.consignment_id) for c in calls] == [
        ("/v1/deliveries/0080034519/ship", m["id"], None, a.id),
        ("/v1/deliveries/0080034520/ship", m["id"], None, b.id)]
    assert calls[0].body == {"message_id": str(calls[0].message_id), "owner": "DEFAULT",
                             "carrier": RIDGELINE, "tracking_no": "RX-77310042",
                             "shipped_by": "dee"}
    h = client.get(f"/v1/consignments/{a.id}", headers=dispatcher_headers).json()["history"]
    assert (h[-1]["kind"], h[-1]["detail"]) == ("collected", {
        "carrier": RIDGELINE, "con_note": "RX-77310042", "manifest": m["manifest_no"],
        "manifest_id": m["id"], "by": "dee"})


def test_the_worker_tells_simple_wms_as_for_a_run(client, db, bay, dispatcher_headers,
                                                  monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    db.execute(OutboundCall.__table__.update().values(next_attempt_at=T0))
    db.commit()
    http = FakeHttp()
    assert worker.send_ship_calls(db, http.client, now=T0) == 2
    assert [(b["carrier"], b["tracking_no"]) for b in http.bodies()] == [
        (RIDGELINE, "RX-77310042"), (RIDGELINE, "RX-77310043")]
    got = client.get(f"/v1/manifests/{m['id']}", headers=dispatcher_headers).json()
    assert got["shipping"] == {"pending": 0, "done": 2, "failed": 0}
    http.client.close()


def test_the_same_message_again_gets_the_same_answer_and_ships_nothing_more(
        client, db, bay, dispatcher_headers):
    m, a, b = bay
    message_id = uuid.uuid4()
    first = collect(client, dispatcher_headers, m, [a.id, b.id], message_id)
    again = collect(client, dispatcher_headers, m, [a.id, b.id], message_id)
    assert again.status_code == 200 and again.json() == first.json()
    assert len(_calls(db)) == 2


def test_collected_twice_from_two_desks_is_collected_once(client, db, bay, dispatcher_headers):
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    other = collect(client, dispatcher_headers, m, [b.id, a.id])
    assert other.status_code == 200 and other.json()["status"] == "collected"
    assert len(_calls(db)) == 2
    stale = collect(client, dispatcher_headers, m, [a.id])
    assert stale.status_code == 409 and stale.json()["code"] == "manifest_collected"


def test_only_what_the_dispatcher_saw(client, db, bay, dispatcher_headers):
    m, a, b = bay
    r = collect(client, dispatcher_headers, m, [a.id])
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert r.json()["detail"] == (f"{m['manifest_no']} changed while you were looking; it has been "
                                  "reloaded, so check it and try again")
    assert _calls(db) == []


def test_not_without_every_con_note(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    a = consignment(db, ref="0080034519", customer="Mill Lane Florist")
    b = consignment(db, ref="0080034520", customer="Hilltop Joinery")
    book(client, dispatcher_headers, rx["id"], a.id)
    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042").json()
    r = collect(client, dispatcher_headers, m, [a.id, b.id])
    assert r.status_code == 409 and r.json()["code"] == "con_note_missing"
    assert r.json()["detail"] == ("Add the con note of Mill Lane Florist (0080034519) first: "
                                  "each consignment needs one before the carrier takes it")
    # the same message can go again once the con note is in: a refusal isn't kept
    message_id = uuid.uuid4()
    collect(client, dispatcher_headers, m, [a.id, b.id], message_id)
    con_note(client, dispatcher_headers, m["id"], a.id, "RX-77310041")
    assert collect(client, dispatcher_headers, m, [a.id, b.id], message_id).status_code == 200


def test_nothing_to_collect(client, db, bay, dispatcher_headers):
    m, a, b = bay
    for c in (a, b):
        client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                    json={"consignment_id": c.id})
    r = collect(client, dispatcher_headers, m, [])
    assert r.status_code == 409 and r.json()["code"] == "manifest_empty"


def test_once_collected_it_stays_as_it_went(client, db, bay, manager_headers, dispatcher_headers):
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    for r in (con_note(client, dispatcher_headers, m["id"], a.id, "RX-77310049"),
              client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                          json={"consignment_id": a.id})):
        assert r.status_code == 409 and r.json()["code"] == "manifest_collected"
    rx = m["carrier"]["id"]
    r = book(client, dispatcher_headers, rx, a.id)
    assert r.status_code == 409 and r.json()["code"] == "not_plannable"
    assert r.json()["detail"] == ("Hilltop Joinery (0080034519) has been collected by a carrier, "
                                  "so it can't go with a carrier")
    # the carrier calls again later in the day: a new manifest
    later = book(client, dispatcher_headers, rx, consignment(db, ref="0080034599").id).json()
    assert later["id"] != m["id"] and later["date"] == DAY.isoformat()


def test_ship_calls_that_gave_up_are_tried_again_on_request(client, db, bay, dispatcher_headers):
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    db.execute(OutboundCall.__table__.update().values(status="failed", attempts=5,
                                                      last_error="HTTP 503: down"))
    db.commit()
    r = client.post(f"/v1/manifests/{m['id']}/retry-ship", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["shipping"] == {"pending": 2, "done": 0, "failed": 0}
    assert {(c.attempts, c.last_error) for c in _calls(db)} == {(0, None)}
    assert all(c.next_attempt_at <= datetime.now(UTC) + timedelta(seconds=1) for c in _calls(db))


def test_one_back_from_its_run_and_collected_stays_shipped_by_its_run_in_simple_wms(
        client, db, manager_headers, dispatcher_headers, monkeypatch):
    """Simple WMS heard it shipped when its run left; when the carrier takes
    it after it came back, Simple WMS answers already_shipped and keeps Own
    fleet and the run number. Done, and said so."""
    from deliveries_samples import failed, on_the_road
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    later = datetime.now(UTC) + timedelta(minutes=1)
    http = FakeHttp()
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    assert worker.send_ship_calls(db, http.client, now=later) == 2
    failed(client, kim, run["id"], b, reason="closed")
    rx = new_carrier(client, manager_headers)
    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042").json()
    assert m["already_shipped"] == []
    collect(client, dispatcher_headers, m, [b.id])
    http.answers.append((409, {"detail": f"{b.wms_ref} has already shipped",
                               "code": "already_shipped"}))
    assert worker.send_ship_calls(db, http.client, now=later) == 1
    call = _calls(db)[-1]
    assert (call.manifest_id, call.status, call.already_shipped) == (m["id"], "done", True)
    assert not any(c.already_shipped for c in _calls(db)[:-1])
    got = client.get(f"/v1/manifests/{m['id']}", headers=dispatcher_headers).json()
    assert got["shipping"] == {"pending": 0, "done": 1, "failed": 0}
    assert got["already_shipped"] == [{"consignment_id": b.id, "wms_ref": b.wms_ref,
                                       "customer": b.customer, "carrier": "Own fleet",
                                       "tracking_no": run["run_no"]}]
    http.client.close()
