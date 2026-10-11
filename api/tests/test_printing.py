"""Run sheets: queued as a template name and JSON, sent to Platen by the
worker, and Platen says how each one went."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import bearer
from fake_http import FakeHttp
from runs_samples import assign, consignment, driver, new_run, vehicle
from tms import worker
from tms.cli import main
from tms.models import PrintJob

T0 = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)


@pytest.fixture
def platen(monkeypatch):
    from tms.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "platen_url", "http://platen.test/intake/jobs")
    monkeypatch.setattr(s, "platen_key", "plt_test-key")
    monkeypatch.setattr(s, "run_sheet_printer", "Office")
    fake = FakeHttp()
    yield fake
    fake.client.close()


@pytest.fixture
def run(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    a = consignment(db, ref="0080034519", customer="Station Deli", suburb="Ballan")
    b = consignment(db, ref="0080034522", customer="Paddock Produce", suburb="Clunes", kg="210")
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    return run


def _print(client, headers, run_id):
    return client.post(f"/v1/runs/{run_id}/print", headers=headers)


def _job(db) -> PrintJob:
    db.expire_all()
    return db.execute(select(PrintJob)).scalar_one()


def test_printing_queues_the_run_sheet(client, db, platen, run, dispatcher_headers):
    r = _print(client, dispatcher_headers, run["id"])
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending" and r.json()["printer"] == "Office"
    job = _job(db)
    assert (job.template, job.version, job.copies) == ("run-sheet", "v1", 1)
    assert job.reference == {"type": "run", "ref": run["run_no"]}
    assert job.data == {
        "run_no": run["run_no"], "date": "2026-10-14", "vehicle": "TMS041",
        "vehicle_type": "van", "driver": "Kim", "driver_code": "kim", "stop_count": 2,
        "total_kg": "310", "total_m3": "1", "max_kg": "1,200", "max_m3": "9",
        "lines": [
            {"stop": 1, "customer": "Station Deli", "wms_ref": "0080034519",
             "address": "1 Main St", "suburb": "Ballan", "postcode": "3342", "phone": "",
             "packages": 1, "weight_kg": "100", "required_by": ""},
            {"stop": 2, "customer": "Paddock Produce", "wms_ref": "0080034522",
             "address": "1 Main St", "suburb": "Clunes", "postcode": "3342", "phone": "",
             "packages": 1, "weight_kg": "210", "required_by": ""},
        ]}
    sheet = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["run_sheet"]
    assert sheet["status"] == "pending" and sheet["job_id"] == str(job.job_id)


def test_the_worker_hands_it_to_platen(client, db, platen, run, dispatcher_headers):
    _print(client, dispatcher_headers, run["id"])
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    assert worker.send_print_jobs(db, platen.client, now=T0) == 1
    sent = platen.requests[0]
    assert str(sent.url) == "http://platen.test/intake/jobs"
    assert sent.headers["Authorization"] == "Bearer plt_test-key"
    job = _job(db)
    assert sent.headers["X-TMS-Job-Id"] == str(job.job_id)
    assert platen.bodies()[0] == {"job_id": str(job.job_id), "template": "run-sheet",
                                  "version": "v1", "printer": "Office", "copies": 1,
                                  "reference": {"type": "run", "ref": run["run_no"]},
                                  "data": job.data}
    assert job.status == "accepted" and job.sent_at == T0


def test_platens_refusal_is_kept_in_its_words_and_retried(client, db, platen, run,
                                                          dispatcher_headers):
    _print(client, dispatcher_headers, run["id"])
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    platen.answers.append((422, {"detail": "template: Platen has no template 'run-sheet'; "
                                           "make one with that id and publish it"}))
    worker.send_print_jobs(db, platen.client, now=T0)
    job = _job(db)
    assert job.status == "pending" and job.next_attempt_at == T0 + timedelta(minutes=1)
    assert job.last_error == ("Platen answered 422: template: Platen has no template "
                              "'run-sheet'; make one with that id and publish it")


def test_without_platen_set_up_printing_says_so(client, db, run, dispatcher_headers):
    r = _print(client, dispatcher_headers, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "printing_not_set_up"
    assert "TMS_PLATEN_URL" in r.json()["detail"]


def test_an_overloaded_run_does_not_print(client, db, platen, dispatcher_headers, run):
    heavy = consignment(db, ref="0080034599", kg="1000")
    assign(client, dispatcher_headers, run["id"], heavy.id)
    r = _print(client, dispatcher_headers, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert "110 kg too heavy" in r.json()["detail"]
    assert db.execute(select(PrintJob)).first() is None


def _platen_key(capsys) -> dict:
    assert main(["create-api-key", "--name", "platen", "--scopes", "printing:write"]) == 0
    return bearer(capsys.readouterr().out.strip().splitlines()[-1])


def test_platen_says_it_printed(client, db, platen, run, dispatcher_headers, capsys):
    key = _platen_key(capsys)
    job_id = _print(client, dispatcher_headers, run["id"]).json()["job_id"]
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=key, json={"status": "printed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "printed" and r.json()["printed_at"] is not None
    # a late "accepted" does not undo it
    late = client.post(f"/v1/print-jobs/{job_id}/status", headers=key,
                       json={"status": "accepted"})
    assert late.json()["status"] == "printed"


def test_platen_says_it_failed_and_why(client, db, platen, run, dispatcher_headers, capsys):
    job_id = _print(client, dispatcher_headers, run["id"]).json()["job_id"]
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=_platen_key(capsys),
                    json={"status": "failed", "message": "Office: out of paper"})
    assert r.json()["status"] == "failed" and r.json()["last_error"] == "Office: out of paper"
    sheet = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["run_sheet"]
    assert sheet["status"] == "failed" and sheet["last_error"] == "Office: out of paper"


def test_only_a_key_with_printing_write_reports(client, db, platen, run, dispatcher_headers):
    job_id = _print(client, dispatcher_headers, run["id"]).json()["job_id"]
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=dispatcher_headers,
                    json={"status": "printed"})
    assert r.status_code == 403
    assert client.post(f"/v1/print-jobs/{job_id}/status",
                       json={"status": "printed"}).status_code == 401


def test_an_unknown_job_is_404_so_platen_stops_asking(client, db, capsys):
    r = client.post(f"/v1/print-jobs/{uuid.uuid4()}/status", headers=_platen_key(capsys),
                    json={"status": "printed"})
    assert r.status_code == 404


def test_sent_at_is_when_platen_answered(client, db, platen, run, dispatcher_headers,
                                         monkeypatch):
    import httpx

    from test_ship_calls import SlowClock
    _print(client, dispatcher_headers, run["id"])
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    clock = SlowClock(T0)
    monkeypatch.setattr(worker, "clock", clock)
    with httpx.Client(transport=clock.transport([])) as h:
        assert worker.send_print_jobs(db, h) == 1
    assert _job(db).sent_at == T0 + timedelta(minutes=5)
