"""The pre-start checklist for each type: the design's to start with, and a
fleet manager's own after that. A line that goes is retired, never deleted."""
from sqlalchemy import select

from deliveries_samples import driver_headers
from runs_samples import driver
from tms.models import ChecklistItem

VAN = [{"label": "Tyres", "critical": False}, {"label": "Lights", "critical": True},
       {"label": "Brakes", "critical": True}, {"label": "Mirrors", "critical": False},
       {"label": "Load restraints", "critical": False}, {"label": "Damage", "critical": False}]


def test_anyone_who_sees_the_fleet_sees_the_checklists(client, db, dispatcher_headers):
    driver(db)
    for headers in (dispatcher_headers, driver_headers(client)):
        r = client.get("/v1/checklists", headers=headers)
        assert r.status_code == 200, r.text
        assert list(r.json()) == ["van", "rigid", "ute"]
        assert [{"label": i["label"], "critical": i["critical"]} for i in r.json()["van"]] == VAN
    assert [i["id"] for i in r.json()["rigid"]] == [7, 8, 9, 10, 11, 12]


def test_a_fleet_manager_changes_a_types_list_and_what_goes_is_retired(client, db,
                                                                       manager_headers):
    new = [{"label": "Brakes", "critical": True}, {"label": "Tyres", "critical": False},
           {"label": "Tail lift", "critical": True}]
    r = client.put("/v1/checklists/van", headers=manager_headers, json={"items": new})
    assert r.status_code == 200, r.text
    # the same line keeps its id, in its new place; a new line gets a new one
    assert r.json() == [{"id": 3, "label": "Brakes", "critical": True},
                        {"id": 1, "label": "Tyres", "critical": False},
                        {"id": 19, "label": "Tail lift", "critical": True}]
    # Lights was critical and isn't now: a different line
    r = client.put("/v1/checklists/van", headers=manager_headers,
                   json={"items": [{"label": "Lights"}]})
    assert r.json() == [{"id": 20, "label": "Lights", "critical": False}]
    rows = db.execute(select(ChecklistItem).where(ChecklistItem.vehicle_type == "van")
                      .order_by(ChecklistItem.id)).scalars().all()
    assert len(rows) == 8 and [r.id for r in rows if r.retired_at is None] == [20]
    # the other types are as they were
    assert len(client.get("/v1/checklists", headers=manager_headers).json()["rigid"]) == 6


def test_only_a_fleet_manager_changes_a_list_and_it_must_make_sense(client, manager_headers,
                                                                    dispatcher_headers):
    body = {"items": [{"label": "Tyres"}]}
    assert client.put("/v1/checklists/van", headers=dispatcher_headers, json=body).status_code == 403
    for bad in ({"items": []}, {"items": [{"label": "Tyres"}, {"label": "tyres "}]},
                {"items": [{"label": " "}]}):
        assert client.put("/v1/checklists/van", headers=manager_headers, json=bad).status_code == 422
    assert client.put("/v1/checklists/bicycle", headers=manager_headers, json=body).status_code == 422
