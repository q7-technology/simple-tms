"""A truck or a driver with a run not finished isn't deactivated: the run
couldn't leave, and a driver on the road would be signed out with
deliveries still to send. Done, or cancelled, it may go."""
import uuid

from deliveries_samples import delivered, on_the_road, planned_run
from runs_samples import driver, new_run, vehicle


def _refused(r, detail):
    assert r.status_code == 409, r.text
    assert r.json() == {"code": "on_a_run", "detail": detail}


def test_a_truck_on_a_planned_run_stays_until_the_run_has_another(client, db, dispatcher_headers,
                                                                  manager_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    r = client.post("/v1/vehicles/tms041/deactivate", headers=manager_headers)
    _refused(r, "TMS041 is on R-0001 (planned for 14 Oct 2026). "
                "Give that run another truck, or cancel it, first.")
    assert client.get("/v1/vehicles/TMS041", headers=manager_headers).json()["active"] is True
    # given another truck, the first may go
    vehicle(db, rego="TMS018")
    r = client.patch(f"/v1/runs/{run['id']}", headers=dispatcher_headers, json={"vehicle": "TMS018"})
    assert r.status_code == 200, r.text
    r = client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False


def test_a_driver_on_the_road_stays_until_the_run_is_finished(client, db, dispatcher_headers,
                                                             manager_headers):
    run, cons, kim = on_the_road(client, db, dispatcher_headers, n=1)
    r = client.post("/v1/drivers/KIM/deactivate", headers=manager_headers)
    _refused(r, "Kim (kim) is on R-0001 (on the road now). "
                "Deactivate them once that run is finished.")
    # their phone still works
    assert delivered(client, kim, run["id"], cons[0]).status_code == 200
    r = client.post(f"/v1/runs/{run['id']}/finish", headers=kim,
                    json={"message_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    r = client.post("/v1/drivers/kim/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False


def test_every_run_not_finished_is_named(client, db, dispatcher_headers, manager_headers):
    from datetime import date

    vehicle(db)
    driver(db)
    new_run(client, dispatcher_headers, day=date(2026, 10, 20))
    vehicle(db, rego="TMS018")
    new_run(client, dispatcher_headers, rego="TMS018", day=date(2026, 10, 21))
    r = client.post("/v1/drivers/kim/deactivate", headers=manager_headers)
    _refused(r, "Kim (kim) is on R-0001 (planned for 20 Oct 2026), "
                "R-0002 (planned for 21 Oct 2026). "
                "Give those runs another driver, or cancel them, first.")


def test_a_cancelled_run_doesnt_hold_its_truck_or_driver(client, db, dispatcher_headers,
                                                         manager_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    assert client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers).status_code == 200
    assert client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers).status_code == 200
    assert client.post("/v1/drivers/kim/deactivate", headers=manager_headers).status_code == 200


def test_deactivating_takes_the_planning_lock(client, db, engine, dispatcher_headers,
                                              manager_headers):
    """A run being made for the truck at that moment is either seen (and the
    deactivation refused) or made after it (and refused itself)."""
    from sqlalchemy import text

    from conftest import blocked_on_a_lock, fresh_client, in_background
    from tms.services.runs import PLANNING_LOCK

    vehicle(db)
    with engine.connect() as holder:
        holder.execute(text("select pg_advisory_xact_lock(:k)"), {"k": PLANNING_LOCK})
        future = in_background(lambda: fresh_client().post(
            "/v1/vehicles/TMS041/deactivate", headers=manager_headers))
        assert blocked_on_a_lock(engine, future)
        holder.rollback()
    assert future.result(timeout=10).status_code == 200
