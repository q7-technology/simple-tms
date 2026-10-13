"""The driver starts the run: a planned run departs, as the dispatcher's
"Leaves now" would; one already gone is left as it is."""
import uuid

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import driver_headers, on_the_road, planned_run, prestart
from runs_samples import driver
from tms.models import InboundMessage, OutboundCall


def _start(client, headers, run_id, message_id=None):
    return client.post(f"/v1/runs/{run_id}/start", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4())})


def test_starting_a_planned_run_departs_it(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    prestart(client, kim, run["id"])
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
    prestart(client, kim, run["id"])
    message_id = uuid.uuid4()
    first = _start(client, kim, run["id"], message_id)
    again = _start(client, kim, run["id"], message_id)
    assert again.status_code == 200 and again.json() == first.json()
    seen = db.execute(select(InboundMessage).where(InboundMessage.message_id == message_id)
                      ).scalar_one()
    assert seen.duplicates == 1 and seen.sender.startswith("driver:")


def test_a_start_sent_four_times_at_once_departs_once(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    prestart(client, kim, run["id"])
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
    prestart(client, driver_headers(client), run["id"])
    _start(client, driver_headers(client), run["id"], message_id)
    driver(db, code="mel", name="Mel")
    r = _start(client, driver_headers(client, code="mel"), run["id"], message_id)
    assert r.status_code == 409 and r.json()["code"] == "message_id_taken"


def test_an_over_full_run_is_not_started_from_the_phone(client, db, dispatcher_headers):
    from runs_samples import assign, consignment
    run, _ = planned_run(client, db, dispatcher_headers)
    heavy = consignment(db, ref="0080039999", kg="1100")
    assign(client, dispatcher_headers, run["id"], heavy.id)
    prestart(client, driver_headers(client), run["id"])
    r = _start(client, driver_headers(client), run["id"])
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert db.execute(select(InboundMessage).where(
        InboundMessage.path.endswith("/start"))).first() is None


def test_a_truck_swapped_while_starting_needs_its_own_check(client, db, engine,
                                                            dispatcher_headers):
    """Start waits for a change of truck under way, then sees it: the new truck
    has had no pre-start, so the run doesn't leave on the old one's check."""
    from sqlalchemy.orm import Session

    from conftest import blocked_on_a_lock, in_background
    from runs_samples import vehicle
    from tms.services import runs as planning
    run, _ = planned_run(client, db, dispatcher_headers)
    vehicle(db, rego="TMS052")
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"]).status_code == 200
    with Session(engine) as other:
        planning.change(other, run["id"], rego="TMS052", driver_code=None)   # not committed yet

        def start_now():
            with fresh_client() as c:
                return _start(c, kim, run["id"])

        starting = in_background(start_now)
        assert blocked_on_a_lock(engine, starting), "start did not wait for planning"
        other.commit()
    r = starting.result(timeout=10)
    assert r.status_code == 409, r.text
    assert r.json() == {"code": "prestart_needed", "detail": f"Do the pre-start check of TMS052 "
                                                             f"before starting {run['run_no']}."}
    assert db.execute(select(OutboundCall)).first() is None
