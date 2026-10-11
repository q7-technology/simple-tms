"""The driver starts the run: a planned run departs, as the dispatcher's
"Leaves now" would; one already gone is left as it is."""
import uuid

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import driver_headers, on_the_road, planned_run
from runs_samples import driver
from tms.models import InboundMessage, OutboundCall


def _start(client, headers, run_id, message_id=None):
    return client.post(f"/v1/runs/{run_id}/start", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4())})


def test_starting_a_planned_run_departs_it(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = uuid.uuid4()
    r = _start(client, kim, run["id"], message_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["message_id"] == str(message_id) and out["run_no"] == run["run_no"]
    assert out["status"] == "departed" and out["departed_at"] is not None
    calls = db.execute(select(OutboundCall)).scalars().all()
    assert len(calls) == 2 and {c.body["shipped_by"] for c in calls} == {"kim"}
    h = client.get(f"/v1/consignments/{cons[0].id}", headers=dispatcher_headers).json()["history"]
    assert h[-1]["kind"] == "departed" and h[-1]["detail"]["by"] == "kim"


def test_starting_a_run_the_office_already_sent_changes_nothing(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    r = _start(client, kim, run["id"])
    assert r.status_code == 200 and r.json()["status"] == "departed"
    assert len(db.execute(select(OutboundCall)).scalars().all()) == 2


def test_the_same_start_twice_gets_the_same_reply(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = uuid.uuid4()
    first = _start(client, kim, run["id"], message_id)
    again = _start(client, kim, run["id"], message_id)
    assert again.status_code == 200 and again.json() == first.json()
    seen = db.execute(select(InboundMessage)).scalar_one()
    assert seen.message_id == message_id and seen.duplicates == 1 and seen.sender.startswith("driver:")


def test_a_start_sent_four_times_at_once_departs_once(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = str(uuid.uuid4())

    def start():
        with fresh_client() as c:
            r = c.post(f"/v1/runs/{run['id']}/start", headers=kim, json={"message_id": message_id})
            return r.status_code, r.json()

    answers = all_at_once(4, start)
    assert [code for code, _ in answers] == [200] * 4
    assert all(body == answers[0][1] for _, body in answers)
    assert len(db.execute(select(OutboundCall)).scalars().all()) == 2


def test_a_driver_starts_only_their_own_run(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    assert _start(client, driver_headers(client, code="mel"), run["id"]).status_code == 404
    assert _start(client, dispatcher_headers, run["id"]).status_code == 403


def test_a_message_id_is_one_senders(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    message_id = uuid.uuid4()
    _start(client, driver_headers(client), run["id"], message_id)
    driver(db, code="mel", name="Mel")
    r = _start(client, driver_headers(client, code="mel"), run["id"], message_id)
    assert r.status_code == 409 and r.json()["code"] == "message_id_taken"


def test_an_over_full_run_is_not_started_from_the_phone(client, db, dispatcher_headers):
    from runs_samples import assign, consignment
    run, _ = planned_run(client, db, dispatcher_headers)
    heavy = consignment(db, ref="0080039999", kg="1100")
    assign(client, dispatcher_headers, run["id"], heavy.id)
    r = _start(client, driver_headers(client), run["id"])
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert db.execute(select(InboundMessage)).first() is None
