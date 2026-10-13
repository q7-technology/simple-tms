"""A run on the road and a driver's phone: what the delivery tests start from."""
import uuid
from datetime import UTC, datetime

from conftest import bearer
from runs_samples import DAY, assign, consignment, driver, new_run, vehicle

# only the first bytes are looked at, so these are pictures enough
PNG = b"\x89PNG\r\n\x1a\n" + b"signature" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"photo" * 40


def driver_headers(client, code="kim", pin="2468") -> dict:
    r = client.post("/v1/auth/driver-login", json={"code": code, "pin": pin})
    assert r.status_code == 200, r.text
    return bearer(r.json()["token"])


def planned_run(client, db, headers, n=2, day=DAY):
    """TMS041 for Kim with n stops, not yet left."""
    vehicle(db)
    driver(db)
    run = new_run(client, headers, day=day)
    cons = [consignment(db, ref=f"00800345{i:02d}", customer=f"Customer {i}") for i in range(n)]
    for c in cons:
        assert assign(client, headers, run["id"], c.id).status_code == 200
    return run, cons


def on_the_road(client, db, headers, n=2):
    """As planned_run, departed by the dispatcher; and Kim's phone signed in."""
    run, cons = planned_run(client, db, headers, n)
    assert client.post(f"/v1/runs/{run['id']}/depart", headers=headers).status_code == 200
    return run, cons, driver_headers(client)


def put_file(client, headers, kind="signature", data=PNG, file_id=None,
             content_type="image/png"):
    file_id = file_id or uuid.uuid4()
    r = client.put(f"/v1/pod-files/{file_id}", params={"kind": kind}, content=data,
                   headers={**headers, "Content-Type": content_type})
    return file_id, r


def now() -> str:
    return datetime.now(UTC).isoformat()


def delivered(client, headers, run_id, c, message_id=None, **over):
    """Deliver consignment c with a signature, Kim's way."""
    body = {"message_id": str(message_id or uuid.uuid4()), "at": now(),
            "packages": [{"package_no": p.package_no, "how": "tick"} for p in c.packages]}
    if "signature_file" not in over and "photo_file" not in over:
        sig, r = put_file(client, headers)
        assert r.status_code == 201, r.text
        body |= {"signature_file": str(sig), "signed_name": "Priya"}
    body |= over
    return client.post(f"/v1/runs/{run_id}/stops/{c.id}/deliver", headers=headers, json=body)


def failed(client, headers, run_id, c, message_id=None, **over):
    body = {"message_id": str(message_id or uuid.uuid4()), "at": now(), "reason": "closed",
            **over}
    return client.post(f"/v1/runs/{run_id}/stops/{c.id}/fail", headers=headers, json=body)


# the design's checklist for a van, as a fresh install has it: (id, label)
VAN_ITEMS = [(1, "Tyres"), (2, "Lights"), (3, "Brakes"), (4, "Mirrors"), (5, "Load restraints"),
             (6, "Damage")]


def prestart(client, headers, run_id, km=61208, failed=(), message_id=None, items=VAN_ITEMS,
             vehicle="TMS041"):
    """Kim's pre-start of the van she was shown (TMS041 unless told): every
    item passed but those named, each failed one with its own photo (sent
    first) and a note."""
    answers = []
    for item_id, label in items:
        if label in failed:
            photo, r = put_file(client, headers, kind="photo", data=JPEG,
                                content_type="image/jpeg")
            assert r.status_code == 201, r.text
            answers.append({"item_id": item_id, "result": "fail", "note": f"{label} not right",
                            "photo_file": str(photo)})
        else:
            answers.append({"item_id": item_id, "result": "pass"})
    body = {"message_id": str(message_id or uuid.uuid4()), "at": now(), "vehicle": vehicle,
            "odometer_km": km, "items": answers}
    return client.post(f"/v1/runs/{run_id}/prestart", headers=headers, json=body)
