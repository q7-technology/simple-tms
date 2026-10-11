"""A run leaves: its consignments are departed and a ship call to Simple WMS
waits in the queue for each, made once however often depart is pressed."""
import uuid

from sqlalchemy import select

from runs_samples import assign, consignment, driver, new_run, vehicle
from tms.models import OutboundCall


def _loaded_run(client, db, headers, kg="100"):
    vehicle(db)
    driver(db)
    run = new_run(client, headers)
    a = consignment(db, ref="0080034519", kg=kg)
    b = consignment(db, ref="0080034522", customer="Paddock Produce", owner="ACME")
    assign(client, headers, run["id"], a.id)
    assign(client, headers, run["id"], b.id)
    return run, a, b


def test_departing_queues_one_ship_call_per_consignment(client, db, dispatcher_headers):
    run, a, b = _loaded_run(client, db, dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "departed" and out["departed_at"] is not None
    assert [s["consignment"]["status"] for s in out["stops"]] == ["departed", "departed"]
    assert out["shipping"] == {"pending": 2, "done": 0, "failed": 0}

    calls = db.execute(select(OutboundCall).order_by(OutboundCall.id)).scalars().all()
    assert [c.path for c in calls] == ["/v1/deliveries/0080034519/ship",
                                       "/v1/deliveries/0080034522/ship"]
    first = calls[0]
    assert first.kind == "ship" and first.status == "pending" and first.attempts == 0
    assert first.body == {"message_id": str(first.message_id), "owner": "DEFAULT",
                          "carrier": "Own fleet", "tracking_no": run["run_no"],
                          "shipped_by": "dee"}
    assert calls[1].body["owner"] == "ACME"
    assert first.message_id != calls[1].message_id
    assert isinstance(first.message_id, uuid.UUID)


def test_departing_is_in_each_history(client, db, dispatcher_headers):
    run, a, _ = _loaded_run(client, db, dispatcher_headers)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    h = client.get(f"/v1/consignments/{a.id}", headers=dispatcher_headers).json()["history"]
    assert h[-1]["kind"] == "departed"
    assert h[-1]["detail"] == {"run": run["run_no"], "run_id": run["id"], "vehicle": "TMS041",
                               "driver": "kim", "by": "dee"}


def test_departing_twice_queues_nothing_more(client, db, dispatcher_headers):
    run, *_ = _loaded_run(client, db, dispatcher_headers)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    again = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert again.status_code == 200 and again.json()["status"] == "departed"
    assert len(db.execute(select(OutboundCall)).scalars().all()) == 2


def test_a_departed_run_keeps_its_stops(client, db, dispatcher_headers):
    run, a, _ = _loaded_run(client, db, dispatcher_headers)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/unassign", headers=dispatcher_headers,
                    json={"consignment_id": a.id})
    assert r.status_code == 409 and r.json()["code"] == "run_not_planned"
    vehicle(db, rego="TMS018")
    other = new_run(client, dispatcher_headers, rego="TMS018")
    moved = assign(client, dispatcher_headers, other["id"], a.id)
    assert moved.status_code == 409 and moved.json()["code"] == "not_plannable"


def test_an_overloaded_run_does_not_leave(client, db, dispatcher_headers):
    run, *_ = _loaded_run(client, db, dispatcher_headers, kg="1150")
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert r.json()["detail"] == (f"{run['run_no']}: 50 kg too heavy. Move a stop to another "
                                  "run before printing the run sheet.")
    assert db.execute(select(OutboundCall)).first() is None


def test_an_empty_run_does_not_leave(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json()["code"] == "run_empty"
