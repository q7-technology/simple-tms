"""Delivered: once, with its proof, however often the phone sends it."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import (
    JPEG, delivered, driver_headers, now, on_the_road, planned_run, prestart, put_file,
)
from runs_samples import driver
from tms.models import ConsignmentEvent, InboundMessage, Pod


def test_a_stop_is_delivered_with_its_proof(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    message_id = uuid.uuid4()
    r = delivered(client, kim, run["id"], a, message_id=message_id, signature_file=str(sig),
                  signed_name="  Priya ", photo_file=str(photo), note="Left at the back door",
                  position={"latitude": "-37.5622104", "longitude": "143.8503", "accuracy_m": "12.34"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert {k: out[k] for k in ("message_id", "run_id", "consignment_id", "sequence", "outcome",
                                "done", "left")} == {
        "message_id": str(message_id), "run_id": run["id"], "consignment_id": a.id,
        "sequence": 1, "outcome": "delivered", "done": 1, "left": 1}

    pod = db.execute(select(Pod)).scalar_one()
    assert pod.id == out["pod_id"] and pod.signed_name == "Priya"
    assert pod.signature_file_id == sig and pod.photo_file_id == photo
    assert pod.packages == [{"package_no": 1, "how": "tick"}]
    assert str(pod.latitude) == "-37.562210" and str(pod.longitude) == "143.850300"
    assert str(pod.accuracy_m) == "12.3" and pod.recorded_by == "kim"

    detail = client.get(f"/v1/consignments/{a.id}", headers=dispatcher_headers).json()
    assert detail["status"] == "delivered"
    last = detail["history"][-1]
    assert last["kind"] == "delivered"
    # no position in the history: it is kept for good, positions are not
    assert last["detail"] == {"run": run["run_no"], "run_id": run["id"], "pod_id": pod.id,
                              "packages": 1, "signed_name": "Priya", "signature_file": str(sig),
                              "photo_file": str(photo), "by": "kim"}

    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert [(s["outcome"], s["reason"]) for s in lane["stops"]] == [("delivered", None), (None, None)]


def test_the_same_delivery_sent_again_is_recorded_once(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    body = {"message_id": str(uuid.uuid4()), "at": now(), "signature_file": str(sig),
            "signed_name": "Priya", "packages": [{"package_no": 1, "how": "tick"}]}
    path = f"/v1/runs/{run['id']}/stops/{a.id}/deliver"
    first = client.post(path, headers=kim, json=body)
    # the reply was lost in a black spot, so the phone sends it again
    again = client.post(path, headers=kim, json=body)
    assert first.status_code == 200, first.text
    assert again.status_code == 200 and again.json() == first.json()
    assert len(db.execute(select(Pod)).scalars().all()) == 1
    kinds = db.execute(select(ConsignmentEvent.kind).where(
        ConsignmentEvent.consignment_id == a.id)).scalars().all()
    assert kinds.count("delivered") == 1
    assert db.execute(select(InboundMessage)).scalar_one().duplicates == 1


def test_a_delivery_sent_four_times_at_once_is_recorded_once(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    body = {"message_id": str(uuid.uuid4()), "at": now(), "signature_file": str(sig),
            "signed_name": "Priya", "packages": [{"package_no": 1, "how": "scan"}]}

    def send():
        with fresh_client() as c:
            r = c.post(f"/v1/runs/{run['id']}/stops/{a.id}/deliver", headers=kim, json=body)
            return r.status_code, r.json()

    answers = all_at_once(4, send)
    assert [code for code, _ in answers] == [200] * 4
    assert all(b == answers[0][1] for _, b in answers)
    assert len(db.execute(select(Pod)).scalars().all()) == 1


def test_a_second_different_confirmation_for_a_stop_is_refused(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    r = delivered(client, kim, run["id"], a)
    assert r.status_code == 409 and r.json() == {
        "code": "stop_done",
        "detail": f"stop 1 on {run['run_no']} is already recorded as delivered"}


def test_nothing_is_delivered_before_the_run_leaves(client, db, dispatcher_headers):
    run, (a, _) = planned_run(client, db, dispatcher_headers)
    r = delivered(client, driver_headers(client), run["id"], a)
    assert r.status_code == 409 and r.json()["code"] == "run_not_started"


def test_every_package_is_named_once(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    r = delivered(client, kim, run["id"], a, packages=[])
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "packages"
    r = delivered(client, kim, run["id"], a, packages=[{"package_no": 1, "how": "tick"}] * 2)
    assert r.status_code == 422


def test_a_delivery_needs_a_signature_and_name_or_a_photo(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    sig, _ = put_file(client, kim)
    nothing = delivered(client, kim, run["id"], a, signature_file=None)
    assert nothing.status_code == 422
    assert "a signature with a printed name, a photo, or both" in nothing.text
    unnamed = delivered(client, kim, run["id"], a, signature_file=str(sig))
    assert unnamed.status_code == 422 and "printed name" in unnamed.text
    assert delivered(client, kim, run["id"], a, photo_file=str(photo)).status_code == 200


def test_a_picture_must_have_been_sent_by_the_same_phone(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    never = delivered(client, kim, run["id"], a, signature_file=str(uuid.uuid4()),
                      signed_name="Priya")
    assert never.status_code == 422 and never.json()["errors"][0]["field"] == "signature_file"
    driver(db, code="mel", name="Mel")
    mels, _ = put_file(client, driver_headers(client, code="mel"))
    theirs = delivered(client, kim, run["id"], a, signature_file=str(mels), signed_name="Priya")
    assert theirs.status_code == 422


def test_a_picture_belongs_to_one_stop(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    assert delivered(client, kim, run["id"], a, signature_file=str(sig),
                     signed_name="Priya").status_code == 200
    r = delivered(client, kim, run["id"], b, signature_file=str(sig), signed_name="Priya")
    assert r.status_code == 422 and "already belongs to another stop" in r.text


def test_the_phones_time_is_kept_but_never_in_the_future(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    earlier = datetime.now(UTC) - timedelta(hours=3)
    delivered(client, kim, run["id"], a, at=earlier.isoformat())
    delivered(client, kim, run["id"], b, at=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    pods = db.execute(select(Pod).order_by(Pod.id)).scalars().all()
    assert abs(pods[0].at - earlier) < timedelta(seconds=1)
    assert pods[1].at <= datetime.now(UTC)
    r = delivered(client, kim, run["id"], a, at="2026-10-14T10:00:00")
    assert r.status_code == 422


def test_a_driver_delivers_only_on_their_own_run(client, db, dispatcher_headers):
    run, (a, _), _ = on_the_road(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    r = delivered(client, driver_headers(client, code="mel"), run["id"], a)
    assert r.status_code == 404


def test_a_message_id_used_for_another_action_is_refused(client, db, dispatcher_headers):
    run, (a, b) = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    prestart(client, kim, run["id"])
    message_id = uuid.uuid4()
    started = client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                          json={"message_id": str(message_id)})
    assert started.status_code == 200, started.text
    # the start's id, sent again on a delivery: not a repeat, a mistake
    r = delivered(client, kim, run["id"], a, message_id=message_id)
    assert r.status_code == 409 and r.json() == {
        "code": "message_id_reused",
        "detail": "that message_id was already used for another confirmation"}
    assert db.execute(select(Pod)).first() is None
    # nor for the same action on another stop
    assert delivered(client, kim, run["id"], a, message_id=uuid.uuid4()).status_code == 200
    first = db.execute(select(Pod.message_id)).scalar_one()
    r = delivered(client, kim, run["id"], b, message_id=first)
    assert r.status_code == 409 and r.json()["code"] == "message_id_reused"
    # a true repeat still gets its own reply back
    again = client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                        json={"message_id": str(message_id)})
    assert again.status_code == 200 and again.json() == started.json()
    assert db.execute(select(InboundMessage).where(
        InboundMessage.message_id == message_id)).scalar_one().duplicates == 1


def test_a_picture_taken_by_another_stop_meanwhile_is_refused_not_a_crash(
        client, db, dispatcher_headers, engine):
    """Two stops naming one signature at once take turns on the picture's row:
    the second waits, then finds it taken."""
    from types import SimpleNamespace

    from sqlalchemy.orm import Session

    from conftest import blocked_on_a_lock, in_background
    from runs_samples import assign, consignment, new_run, vehicle
    from tms.models import Driver
    from tms.services import deliveries

    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    # another run of Kim's, whose stop takes the same signature in a race
    vehicle(db, rego="TMS042")
    other = new_run(client, dispatcher_headers, rego="TMS042")
    c = consignment(db, ref="0080034599", customer="Elsewhere")
    assert assign(client, dispatcher_headers, other["id"], c.id).status_code == 200
    assert client.post(f"/v1/runs/{other['id']}/depart",
                       headers=dispatcher_headers).status_code == 200
    sig, _ = put_file(client, kim)
    d = db.execute(select(Driver).where(Driver.code == "kim")).scalar_one()
    who = SimpleNamespace(kind="driver", id=d.id, name="kim")
    with Session(engine) as rival:
        deliveries.deliver(rival, who, other["id"], c.id, message_id=uuid.uuid4(),
                           at=datetime.now(UTC), packages=[{"package_no": 1, "how": "tick"}],
                           signed_name="Priya", signature_file=sig, photo_file=None, note=None,
                           where=None)                          # not committed yet

        def deliver_now():
            with fresh_client() as c2:
                return delivered(c2, kim, run["id"], a, signature_file=str(sig),
                                 signed_name="Priya")

        delivering = in_background(deliver_now)
        assert blocked_on_a_lock(engine, delivering), "the delivery did not wait for the picture"
        rival.commit()
    r = delivering.result(timeout=10)
    assert r.status_code == 422, r.text
    assert r.json()["errors"] == [{"field": "signature_file",
                                   "message": f"signature {sig} already belongs to another stop"}]
