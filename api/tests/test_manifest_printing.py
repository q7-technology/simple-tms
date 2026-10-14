"""Carrier manifests through Platen, as run sheets go: a template name and
JSON, sent by the worker, and Platen says how it went. Not while a
consignment on it has no con note."""
import pytest
from sqlalchemy import select

from carriers_samples import RIDGELINE, book, new_carrier
from runs_samples import consignment
from test_printing import T0, _platen_key, platen  # noqa: F401  (the fixture)
from tms import worker
from tms.models import PrintJob


@pytest.fixture
def manifest(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers, account="RX-4410", pickup_time="15:30")
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery", suburb="Bacchus Marsh",
                    kg="72")
    b = consignment(db, ref="0080034520", customer="Old Mill Books", suburb="Castlemaine",
                    kg="54")
    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    return book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310043").json()


def _print(client, headers, m):
    return client.post(f"/v1/manifests/{m['id']}/print", headers=headers)


def _job(db) -> PrintJob:
    db.expire_all()
    return db.execute(select(PrintJob)).scalar_one()


def test_printing_queues_the_manifest(client, db, platen, manifest, dispatcher_headers):
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 202, r.text
    out = r.json()
    assert (out["status"], out["printer"], out["run_id"], out["manifest_id"]) == (
        "pending", "Office", None, manifest["id"])
    job = _job(db)
    assert (job.template, job.version, job.copies) == ("carrier-manifest", "v1", 1)
    assert job.reference == {"type": "manifest", "ref": manifest["manifest_no"]}
    assert job.data == {
        "manifest_no": manifest["manifest_no"], "date": "2026-10-14", "carrier": RIDGELINE,
        "account": "RX-4410", "pickup_time": "15:30", "consignment_count": 2,
        "package_count": 2, "total_kg": "126",
        "lines": [
            {"line": 1, "con_note": "RX-77310042", "wms_ref": "0080034519",
             "customer": "Hilltop Joinery", "address": "1 Main St", "suburb": "Bacchus Marsh",
             "state": "VIC", "postcode": "3342", "packages": 1, "weight_kg": "72"},
            {"line": 2, "con_note": "RX-77310043", "wms_ref": "0080034520",
             "customer": "Old Mill Books", "address": "1 Main St", "suburb": "Castlemaine",
             "state": "VIC", "postcode": "3342", "packages": 1, "weight_kg": "54"},
        ]}
    got = client.get(f"/v1/manifests/{manifest['id']}", headers=dispatcher_headers).json()
    assert got["print_job"]["status"] == "pending" and got["print_job"]["job_id"] == str(job.job_id)


def test_its_own_printer_when_one_is_set(client, db, platen, manifest, dispatcher_headers,
                                         monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "manifest_printer", "Bay")
    assert _print(client, dispatcher_headers, manifest).json()["printer"] == "Bay"


def test_not_while_a_con_note_is_missing(client, db, platen, manifest, dispatcher_headers):
    rx = manifest["carrier"]["id"]
    book(client, dispatcher_headers, rx, consignment(db, ref="0080034521",
                                                    customer="Mill Lane Florist").id)
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 409 and r.json()["code"] == "con_note_missing"
    assert r.json()["detail"].startswith("Add the con note of Mill Lane Florist (0080034521) first")
    assert db.execute(select(PrintJob)).first() is None


def test_nothing_to_print_and_no_platen(client, db, manifest, dispatcher_headers, monkeypatch):
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 409 and r.json()["code"] == "printing_not_set_up"
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "platen_url", "http://platen.test/intake/jobs")
    for line in manifest["lines"]:
        client.post(f"/v1/manifests/{manifest['id']}/unbook", headers=dispatcher_headers,
                    json={"consignment_id": line["consignment"]["id"]})
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 409 and r.json()["code"] == "manifest_empty"


def test_the_worker_hands_it_to_platen_and_platen_says_printed(
        client, db, platen, manifest, dispatcher_headers, capsys):
    key = _platen_key(capsys)
    job_id = _print(client, dispatcher_headers, manifest).json()["job_id"]
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    assert worker.send_print_jobs(db, platen.client, now=T0) == 1
    assert platen.bodies()[0]["template"] == "carrier-manifest"
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=key, json={"status": "printed"})
    assert r.status_code == 200 and r.json()["manifest_id"] == manifest["id"]
    got = client.get(f"/v1/manifests/{manifest['id']}", headers=dispatcher_headers).json()
    assert got["print_job"]["status"] == "printed"


def test_a_collected_manifest_prints_again_as_it_went(client, db, platen, manifest,
                                                      dispatcher_headers):
    import uuid
    ids = [line["consignment"]["id"] for line in manifest["lines"]]
    client.post(f"/v1/manifests/{manifest['id']}/collect", headers=dispatcher_headers,
                json={"message_id": str(uuid.uuid4()), "consignment_ids": ids})
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 202
    assert [line["con_note"] for line in _job(db).data["lines"]] == ["RX-77310042", "RX-77310043"]
