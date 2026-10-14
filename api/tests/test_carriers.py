"""Carriers: who else takes freight. Added and changed by a fleet manager,
read by the office, deactivated and never deleted. Booked by hand in version
one; an adapter for a carrier's own API slots in later."""
from carriers_samples import RIDGELINE, VALLEY, new_carrier
from tms.services import carrier_adapters


def test_a_fleet_manager_adds_a_carrier(client, manager_headers):
    r = client.post("/v1/carriers", headers=manager_headers,
                    json={"name": "  Ridgeline Express (sample) ", "account": "RX-4410",
                          "pickup_time": "15:30"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["name"], c["account"], c["pickup_time"], c["adapter"], c["active"]) == (
        RIDGELINE, "RX-4410", "15:30", "manual", True)
    assert client.get(f"/v1/carriers/{c['id']}", headers=manager_headers).json() == c


def test_the_office_lists_them_and_only_a_fleet_manager_changes_them(
        client, manager_headers, dispatcher_headers):
    new_carrier(client, manager_headers, VALLEY)
    new_carrier(client, manager_headers, RIDGELINE)
    listed = client.get("/v1/carriers", headers=dispatcher_headers).json()
    assert [c["name"] for c in listed["items"]] == [RIDGELINE, VALLEY] and listed["total"] == 2
    r = client.post("/v1/carriers", headers=dispatcher_headers, json={"name": "Plains Parcels (sample)"})
    assert r.status_code == 403


def test_a_name_is_one_carrier_whatever_its_case(client, manager_headers):
    new_carrier(client, manager_headers)
    r = client.post("/v1/carriers", headers=manager_headers, json={"name": RIDGELINE.upper()})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "name",
                                   "message": f"carrier {RIDGELINE.upper()} already exists"}]


def test_own_fleet_is_not_a_carrier(client, manager_headers):
    r = client.post("/v1/carriers", headers=manager_headers, json={"name": "own FLEET"})
    assert r.status_code == 422
    assert "Own fleet is what Simple WMS is told for your own trucks" in r.json()["errors"][0]["message"]


def test_an_adapter_there_is_no_code_for_is_refused(client, manager_headers):
    r = client.post("/v1/carriers", headers=manager_headers,
                    json={"name": RIDGELINE, "adapter": "teleport"})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "adapter",
                                   "message": "teleport isn't an adapter Simple TMS has: manual"}]


def test_a_change_keeps_what_is_left_out_and_clears_what_is_null(client, manager_headers):
    c = new_carrier(client, manager_headers, account="RX-4410", pickup_time="15:30")
    r = client.patch(f"/v1/carriers/{c['id']}", headers=manager_headers,
                     json={"pickup_time": "14:00", "account": None})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["pickup_time"], r.json()["account"]) == (RIDGELINE, "14:00", None)
    other = new_carrier(client, manager_headers, VALLEY)
    r = client.patch(f"/v1/carriers/{other['id']}", headers=manager_headers, json={"name": RIDGELINE})
    assert r.status_code == 422


def test_deactivated_not_deleted(client, manager_headers, dispatcher_headers):
    c = new_carrier(client, manager_headers)
    r = client.post(f"/v1/carriers/{c['id']}/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.get("/v1/carriers", headers=dispatcher_headers, params={"active": True}).json()["total"] == 0
    assert client.get("/v1/carriers", headers=dispatcher_headers).json()["total"] == 1
    r = client.post(f"/v1/carriers/{c['id']}/reactivate", headers=manager_headers)
    assert r.json()["active"] is True
    assert client.get("/v1/carriers/99", headers=dispatcher_headers).status_code == 404


def test_the_manual_adapter_books_nothing_a_person_types_the_con_note():
    manual = carrier_adapters.ADAPTERS["manual"]
    assert isinstance(manual, carrier_adapters.Adapter)
    assert manual.book(None, None) is None
    assert manual.track(None, "RX-77310042") == []
    assert list(carrier_adapters.ADAPTERS) == ["manual"]
