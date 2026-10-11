"""Signatures and photos: kept on disk, checked for what they are, sent again
harmlessly, and shown only to who may see them."""
import hashlib
import uuid

from deliveries_samples import JPEG, PNG, driver_headers, put_file
from runs_samples import driver
from tms.services import pod_files


def test_a_signature_is_kept_on_disk_once(client, db, pod_dir):
    driver(db)
    kim = driver_headers(client)
    file_id, r = put_file(client, kim)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["id"] == str(file_id) and out["kind"] == "signature"
    assert out["content_type"] == "image/png" and out["size_bytes"] == len(PNG)
    assert out["sha256"] == hashlib.sha256(PNG).hexdigest()
    stored = list(pod_dir.rglob("*.png"))
    assert [p.name for p in stored] == [f"{file_id}.png"] and stored[0].read_bytes() == PNG

    again, r2 = put_file(client, kim, file_id=file_id)
    assert r2.status_code == 200 and r2.json() == out
    assert len(list(pod_dir.rglob("*.png"))) == 1


def test_other_bytes_under_a_used_id_are_refused(client, db):
    driver(db)
    kim = driver_headers(client)
    file_id, _ = put_file(client, kim)
    _, r = put_file(client, kim, file_id=file_id, data=PNG + b"more")
    assert r.status_code == 409 and r.json()["code"] == "file_id_taken"


def test_only_pictures_of_the_right_kind(client, db):
    driver(db)
    kim = driver_headers(client)
    _, r = put_file(client, kim, kind="signature", data=JPEG, content_type="image/jpeg")
    assert r.status_code == 415 and r.json() == {
        "detail": "a signature must be a PNG picture", "code": "unsupported_type"}
    # what the request says it is counts for nothing; the bytes do
    _, r = put_file(client, kim, kind="photo", data=b"<html>not a photo</html>",
                    content_type="image/jpeg")
    assert r.status_code == 415
    _, r = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    assert r.status_code == 201 and r.json()["content_type"] == "image/jpeg"
    _, r = put_file(client, kim, kind="selfie")
    assert r.status_code == 422


def test_a_file_too_big_is_refused(client, db, monkeypatch):
    driver(db)
    kim = driver_headers(client)
    monkeypatch.setitem(pod_files.KINDS, "signature", ({"image/png"}, 100))
    _, r = put_file(client, kim, data=PNG)
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    monkeypatch.setattr(pod_files, "LARGEST", 50)
    _, r = put_file(client, kim, data=PNG)
    assert r.status_code == 413


def test_only_a_driver_or_a_key_sends_files(client, db, dispatcher_headers):
    _, r = put_file(client, dispatcher_headers)
    assert r.status_code == 403
    _, r = put_file(client, {})
    assert r.status_code == 401


def test_a_file_is_shown_to_the_office_and_to_whoever_sent_it(client, db, dispatcher_headers):
    driver(db)
    driver(db, code="mel", name="Mel")
    kim = driver_headers(client)
    file_id, _ = put_file(client, kim)
    for headers in (kim, dispatcher_headers):
        r = client.get(f"/v1/pod-files/{file_id}", headers=headers)
        assert r.status_code == 200 and r.content == PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["content-security-policy"] == "default-src 'none'"
    assert client.get(f"/v1/pod-files/{file_id}",
                      headers=driver_headers(client, code="mel")).status_code == 404
    assert client.get(f"/v1/pod-files/{file_id}").status_code == 401
    assert client.get(f"/v1/pod-files/{uuid.uuid4()}", headers=kim).status_code == 404


def test_a_file_missing_from_disk_is_not_found_and_logged(client, db, pod_dir, caplog,
                                                          dispatcher_headers):
    driver(db)
    file_id, _ = put_file(client, driver_headers(client))
    for kept in pod_dir.rglob("*.png"):
        kept.unlink()
    with caplog.at_level("WARNING", logger="tms.pod"):
        r = client.get(f"/v1/pod-files/{file_id}", headers=dispatcher_headers)
    assert r.status_code == 404 and r.json() == {"detail": f"no file {file_id}"}
    assert [rec.levelname for rec in caplog.records if str(file_id) in rec.getMessage()] == [
        "WARNING"]
