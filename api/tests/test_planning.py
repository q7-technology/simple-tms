"""Dragging consignments onto runs and stops into order: idempotent, and
safe with two dispatchers at it at once."""
from sqlalchemy import func, select

from conftest import all_at_once, fresh_client
from runs_samples import assign, consignment, driver, new_run, order, vehicle
from tms.models import Stop


def _depot(client, db, headers, n=3):
    vehicle(db)
    vehicle(db, rego="TMS018")
    driver(db)
    cons = [consignment(db, ref=f"00800345{i:02d}", customer=f"Customer {i}") for i in range(n)]
    return new_run(client, headers), new_run(client, headers, rego="TMS018"), cons


def _history(client, headers, cid) -> list[tuple[str, dict]]:
    h = client.get(f"/v1/consignments/{cid}", headers=headers).json()["history"]
    return [(e["kind"], e["detail"]) for e in h]


def test_a_consignment_goes_on_a_run_last_unless_told_where(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    r = assign(client, dispatcher_headers, run["id"], c.id, position=1)
    assert r.status_code == 200, r.text
    out = r.json()
    assert order(out) == [c.id, a.id, b.id]
    assert [s["sequence"] for s in out["stops"]] == [1, 2, 3]
    stop = out["stops"][0]["consignment"]
    assert stop == {"id": c.id, "wms_ref": c.wms_ref, "customer": "Customer 2", "suburb": "Ballan",
                    "status": "assigned", "packages": 1, "cartons": 1, "weight_kg": "100",
                    "volume_m3": "0.5"}
    assert out["load"]["weight_kg"] == "300" and out["load"]["volume_m3"] == "1.5"


def test_assigning_is_in_the_history_with_who_and_which_run(client, db, dispatcher_headers):
    run, _, (a, *_) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    kind, detail = _history(client, dispatcher_headers, a.id)[-1]
    assert kind == "assigned"
    assert detail == {"run": run["run_no"], "run_id": run["id"], "by": "dee"}


def test_the_same_drop_twice_changes_nothing(client, db, dispatcher_headers):
    run, _, (a, b, _c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    again = assign(client, dispatcher_headers, run["id"], a.id, position=1)
    assert order(again.json()) == [a.id, b.id]
    kinds = [k for k, _ in _history(client, dispatcher_headers, a.id)]
    assert kinds == ["packed", "assigned"]


def test_dropping_a_stop_lower_on_its_own_run_moves_it(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    out = assign(client, dispatcher_headers, run["id"], a.id, position=3).json()
    assert order(out) == [b.id, c.id, a.id]
    assert [k for k, _ in _history(client, dispatcher_headers, a.id)] == ["packed", "assigned"]


def test_dropping_it_on_another_run_moves_it_there(client, db, dispatcher_headers):
    one, two, (a, b, _c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, one["id"], a.id)
    assign(client, dispatcher_headers, one["id"], b.id)
    out = assign(client, dispatcher_headers, two["id"], a.id).json()
    assert order(out) == [a.id]
    left = client.get(f"/v1/runs/{one['id']}", headers=dispatcher_headers).json()
    assert order(left) == [b.id] and left["stops"][0]["sequence"] == 1
    kinds = [(k, d["run"]) for k, d in _history(client, dispatcher_headers, a.id)[1:]]
    assert kinds == [("assigned", one["run_no"]), ("unassigned", one["run_no"]),
                     ("assigned", two["run_no"])]


def test_back_to_the_tray(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    r = client.post(f"/v1/runs/{run['id']}/unassign", headers=dispatcher_headers,
                    json={"consignment_id": b.id})
    assert r.status_code == 200
    assert order(r.json()) == [a.id, c.id]
    assert [s["sequence"] for s in r.json()["stops"]] == [1, 2]
    tray = client.get("/v1/consignments", params={"status": "packed"},
                      headers=dispatcher_headers).json()
    assert [x["id"] for x in tray["items"]] == [b.id]
    again = client.post(f"/v1/runs/{run['id']}/unassign", headers=dispatcher_headers,
                        json={"consignment_id": b.id})
    assert again.status_code == 200 and order(again.json()) == [a.id, c.id]
    assert [k for k, _ in _history(client, dispatcher_headers, b.id)] == [
        "packed", "assigned", "unassigned"]
    # the row stays, with removed_at: cancel, never delete
    assert db.execute(select(func.count()).select_from(Stop)).scalar_one() == 3


def test_reorder_takes_the_whole_order(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [c.id, a.id, b.id], "seen": [a.id, b.id, c.id]})
    assert r.status_code == 200 and order(r.json()) == [c.id, a.id, b.id]
    same = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                       json={"consignment_ids": [c.id, a.id, b.id], "seen": [c.id, a.id, b.id]})
    assert order(same.json()) == [c.id, a.id, b.id]


def test_a_second_reorder_from_the_same_starting_order_is_refused(client, db,
                                                                   dispatcher_headers):
    """Two dispatchers each move a stop on the board as they both drew it: the
    second would silently undo the first, so it is refused and reloads."""
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    first = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                        json={"consignment_ids": [c.id, a.id, b.id], "seen": [a.id, b.id, c.id]})
    assert first.status_code == 200
    second = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                         json={"consignment_ids": [b.id, a.id, c.id], "seen": [a.id, b.id, c.id]})
    assert second.status_code == 409 and second.json()["code"] == "stale"
    now = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert order(now) == [c.id, a.id, b.id]


def test_a_reorder_says_what_it_saw(client, db, dispatcher_headers):
    run, _, (a, b, _c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [b.id, a.id]})
    assert r.status_code == 422


def test_an_order_from_before_someone_else_changed_the_run_is_refused(client, db,
                                                                      dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    assign(client, dispatcher_headers, run["id"], c.id)     # someone else, meanwhile
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [b.id, a.id], "seen": [a.id, b.id]})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert "changed while you were looking" in r.json()["detail"]
    twice = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                        json={"consignment_ids": [a.id, a.id, b.id], "seen": [a.id, b.id, c.id]})
    assert twice.status_code == 409


def test_an_unknown_consignment_is_named(client, db, dispatcher_headers):
    run, _, _ = _depot(client, db, dispatcher_headers)
    r = assign(client, dispatcher_headers, run["id"], 999)
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "consignment_id", "message": "no consignment 999"}]
    assert assign(client, dispatcher_headers, 999, 1).status_code == 404


def test_a_run_on_a_deactivated_truck_takes_no_more_stops(client, db, manager_headers):
    run, _, (a, *_) = _depot(client, db, manager_headers)
    client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers)
    r = assign(client, manager_headers, run["id"], a.id)
    assert r.status_code == 409 and r.json()["code"] == "vehicle_unavailable"


def test_a_run_with_stops_is_not_cancelled(client, db, dispatcher_headers):
    run, _, (a, *_) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    r = client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json()["code"] == "run_not_empty"


def test_two_dispatchers_dropping_at_once_get_one_clean_order(client, db, dispatcher_headers):
    run, _, cons = _depot(client, db, dispatcher_headers, n=8)
    ids = iter([c.id for c in cons])

    def drop():
        cid = next(ids)
        with fresh_client() as c:
            return assign(c, dispatcher_headers, run["id"], cid, position=1).status_code

    assert all_at_once(8, drop) == [200] * 8
    out = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert sorted(order(out)) == sorted(c.id for c in cons)
    assert [s["sequence"] for s in out["stops"]] == list(range(1, 9))


def test_one_consignment_dropped_on_two_runs_at_once_lands_on_one(client, db,
                                                                   dispatcher_headers):
    one, two, (a, *_) = _depot(client, db, dispatcher_headers)
    targets = iter([one["id"], two["id"]] * 3)

    def drop():
        run_id = next(targets)
        with fresh_client() as c:
            return assign(c, dispatcher_headers, run_id, a.id).status_code

    assert all_at_once(6, drop) == [200] * 6
    live = db.execute(select(func.count()).select_from(Stop)
                      .where(Stop.consignment_id == a.id, Stop.removed_at.is_(None))).scalar_one()
    assert live == 1
