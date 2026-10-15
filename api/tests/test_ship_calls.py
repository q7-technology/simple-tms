"""The worker makes each ship call to Simple WMS, retrying after 1 min, 5, 30
and 2 h; the same message_id goes every time, so Simple WMS ships it once."""
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from fake_http import FakeHttp
from runs_samples import assign, consignment, driver, new_run, vehicle
from tms import worker
from tms.models import OutboundCall

T0 = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)


@pytest.fixture
def http(monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    fake = FakeHttp()
    yield fake
    fake.client.close()


@pytest.fixture
def departed(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    c = consignment(db, ref="0080034519")
    assign(client, dispatcher_headers, run["id"], c.id)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    db.execute(OutboundCall.__table__.update().values(next_attempt_at=T0))
    db.commit()
    return run


def _call(db) -> OutboundCall:
    db.expire_all()
    return db.execute(select(OutboundCall).order_by(OutboundCall.id).limit(1)).scalar_one()


def test_the_ship_call_goes_with_our_key_and_its_message_id(db, http, departed):
    assert worker.send_ship_calls(db, http.client, now=T0) == 1
    sent = http.requests[0]
    assert str(sent.url) == "http://wms.test/v1/deliveries/0080034519/ship"
    assert sent.headers["Authorization"] == "Bearer wms_test-key-for-the-tms"
    row = _call(db)
    assert http.bodies()[0] == {"message_id": str(row.message_id), "owner": "DEFAULT",
                                "carrier": "Own fleet", "tracking_no": departed["run_no"],
                                "shipped_by": "dee"}
    assert row.status == "done" and row.attempts == 1 and row.done_at == T0
    assert row.already_shipped is False


def test_a_failed_try_waits_1_5_30_then_120_minutes_and_the_next_failure_gives_up(
        db, http, departed):
    now = T0
    for attempt, wait in enumerate((1, 5, 30, 120), start=1):
        http.answers.append((503, {"detail": "down"}))
        assert worker.send_ship_calls(db, http.client, now=now) == 1
        row = _call(db)
        assert (row.status, row.attempts) == ("pending", attempt)
        assert row.next_attempt_at == now + timedelta(minutes=wait)
        assert row.last_error.startswith("HTTP 503")
        # not due yet: nothing is tried
        assert worker.send_ship_calls(db, http.client, now=now + timedelta(seconds=30)) == 0
        now = row.next_attempt_at
    http.answers.append((503, {"detail": "still down"}))
    worker.send_ship_calls(db, http.client, now=now)
    row = _call(db)
    assert (row.status, row.attempts) == ("failed", 5)
    assert len({b["message_id"] for b in http.bodies()}) == 1     # the same every time


def test_a_network_error_is_retried_like_a_refusal(db, http, departed):
    http.answers.append(httpx.ConnectError("connection refused"))
    worker.send_ship_calls(db, http.client, now=T0)
    row = _call(db)
    assert row.status == "pending" and row.last_error.startswith("ConnectError")


def test_already_shipped_in_simple_wms_counts_as_done(db, http, departed):
    """Simple WMS did it, and the answer was lost: a retry after it stopped
    remembering the message_id (24 hours) hears this instead of the reply."""
    http.answers.append((409, {"detail": "0080034519 has already shipped",
                               "code": "already_shipped"}))
    worker.send_ship_calls(db, http.client, now=T0)
    assert (_call(db).status, _call(db).already_shipped) == ("done", True)


def test_already_returned_in_simple_wms_counts_as_done(db, http, departed):
    row = _call(db)
    row.kind, row.path = "return", "/v1/deliveries/0080034519/return"
    db.commit()
    http.answers.append((409, {"detail": "0080034519 has already come back",
                               "code": "already_returned"}))
    worker.send_ship_calls(db, http.client, now=T0)
    assert (_call(db).status, _call(db).last_error) == ("done", None)


def test_a_return_simple_wms_refuses_is_retried(db, http, departed):
    """No returns zone in Simple WMS yet, say: it is put right there, and
    the next try goes through."""
    row = _call(db)
    row.kind, row.path = "return", "/v1/deliveries/0080034519/return"
    db.commit()
    http.answers.append((422, {"errors": [{"field": "warehouse", "message": "BAL-WH01 has no returns zone"}]}))
    worker.send_ship_calls(db, http.client, now=T0)
    row = _call(db)
    assert row.status == "pending" and "no returns zone" in row.last_error


def test_a_consignment_s_calls_go_in_the_order_they_were_queued(db, http, departed):
    """A later call for a consignment waits while an earlier one isn't done,
    even when it is due first: Simple WMS has to hear it went before it hears
    it came back, and that before it went again."""
    first = _call(db)
    later_id = uuid.uuid4()
    db.add(OutboundCall(message_id=later_id, kind="return", run_id=first.run_id,
                        consignment_id=first.consignment_id,
                        path="/v1/deliveries/0080034519/return",
                        body={"message_id": str(later_id), "owner": "DEFAULT", "reason": "closed"},
                        status="pending", attempts=0, next_attempt_at=T0 - timedelta(hours=1)))
    first.next_attempt_at = T0 + timedelta(minutes=1)
    db.commit()
    assert worker.send_ship_calls(db, http.client, now=T0) == 0      # the first isn't due
    first = _call_by(db, first.id)
    first.status = "failed"
    db.commit()
    assert worker.send_ship_calls(db, http.client, now=T0) == 0      # nor done: it holds
    first = _call_by(db, first.id)
    first.status, first.next_attempt_at = "pending", T0
    db.commit()
    assert worker.send_ship_calls(db, http.client, now=T0) == 2
    assert [r.url.path for r in http.requests] == ["/v1/deliveries/0080034519/ship",
                                                   "/v1/deliveries/0080034519/return"]


def _call_by(db, call_id) -> OutboundCall:
    db.expire_all()
    return db.get(OutboundCall, call_id)


def test_a_key_without_the_scope_is_logged_as_an_error_and_retried(db, http, departed, caplog):
    http.answers.append((403, {"detail": "you do not have the tasks:write scope"}))
    with caplog.at_level(logging.ERROR, logger="tms.wms"):
        worker.send_ship_calls(db, http.client, now=T0)
    assert "tasks:write" in caplog.text
    assert "wms_test-key-for-the-tms" not in caplog.text
    assert _call(db).status == "pending"


def test_a_404_on_a_return_says_simple_wms_may_have_no_return_endpoint(
        db, http, departed, caplog):
    """An older Simple WMS answers 404 to a path it doesn't have."""
    row = _call(db)
    row.kind, row.path = "return", "/v1/deliveries/0080034519/return"
    db.commit()
    http.answers.append((404, {"detail": "Not Found"}))
    with caplog.at_level(logging.ERROR, logger="tms.wms"):
        worker.send_ship_calls(db, http.client, now=T0)
    assert "no return endpoint yet" in caplog.text


def test_a_404_on_a_ship_says_nothing_of_a_return_endpoint(db, http, departed, caplog):
    http.answers.append((404, {"detail": "no such delivery"}))
    with caplog.at_level(logging.ERROR, logger="tms.wms"):
        worker.send_ship_calls(db, http.client, now=T0)
    assert "no such delivery for that owner" in caplog.text
    assert "return endpoint" not in caplog.text


def test_without_simple_wms_set_up_the_calls_wait_untried(db, http, departed, monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "")
    assert worker.send_ship_calls(db, http.client, now=T0) == 0
    assert http.requests == [] and _call(db).attempts == 0


def test_failed_calls_are_tried_again_on_request(client, db, http, departed,
                                                 dispatcher_headers):
    row = _call(db)
    row.status, row.attempts, row.last_error = "failed", 5, "HTTP 503: down"
    db.commit()
    r = client.post(f"/v1/runs/{departed['id']}/retry-ship", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["shipping"] == {"pending": 1, "done": 0, "failed": 0, "last_error": None, "held": 0, "held_by": []}
    row = _call(db)
    assert row.attempts == 0 and row.status == "pending" and row.last_error is None
    worker.send_ship_calls(db, http.client, now=datetime.now(UTC) + timedelta(seconds=1))
    assert _call(db).status == "done"
    assert http.bodies()[0]["message_id"] == str(row.message_id)


def test_two_workers_at_once_send_each_call_once(client, db, engine, dispatcher_headers,
                                                 monkeypatch):
    """Each claims one row at a time, locked, so the other passes it by."""
    import threading
    import time

    from sqlalchemy.orm import Session

    from conftest import all_at_once
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    for i in range(6):
        c = consignment(db, ref=f"00800345{i:02d}", kg="10")
        assign(client, dispatcher_headers, run["id"], c.id)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    db.execute(OutboundCall.__table__.update().values(next_attempt_at=T0))
    db.commit()

    sent, lock = [], threading.Lock()

    def slow(request):
        time.sleep(0.05)
        with lock:
            sent.append(request.read())
        return httpx.Response(202, json={})

    def one_worker():
        with Session(engine) as s, httpx.Client(transport=httpx.MockTransport(slow)) as h:
            return worker.send_ship_calls(s, h, now=T0)

    assert sum(all_at_once(2, one_worker)) == 6
    assert len(sent) == 6 and len(set(sent)) == 6
    db.expire_all()
    assert {c.status for c in db.execute(select(OutboundCall)).scalars()} == {"done"}


class SlowClock:
    """The worker's clock, moved on five minutes by each answer: a slow batch."""
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def transport(self, answers):
        def answer(request):
            self.now += timedelta(minutes=5)
            status, body = answers.pop(0) if answers else (202, {})
            return httpx.Response(status, json=body)
        return httpx.MockTransport(answer)


def test_times_are_taken_after_each_call_not_at_the_start_of_the_batch(
        client, db, dispatcher_headers, monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    for i in range(2):
        assign(client, dispatcher_headers, run["id"], consignment(db, ref=f"0080034{i}", kg="10").id)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    db.execute(OutboundCall.__table__.update().values(next_attempt_at=T0))
    db.commit()
    clock = SlowClock(T0)
    monkeypatch.setattr(worker, "clock", clock)
    with httpx.Client(transport=clock.transport([(202, {}), (503, {"detail": "down"})])) as h:
        assert worker.send_ship_calls(db, h) == 2
    db.expire_all()
    first, second = db.execute(select(OutboundCall).order_by(OutboundCall.id)).scalars()
    assert first.status == "done" and first.done_at == T0 + timedelta(minutes=5)
    assert second.status == "pending"
    assert second.next_attempt_at == T0 + timedelta(minutes=10) + timedelta(minutes=1)
