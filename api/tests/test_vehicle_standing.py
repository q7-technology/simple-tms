"""Where a truck stands: rego and service due (amber 30 days or 1,000 km
out, red when overdue), its odometer, and grounded by a failed pre-start."""
from datetime import date

import pytest

from fleet_samples import check, record, serviced
from runs_samples import vehicle
from tms.services.fleet import add_months


@pytest.mark.parametrize("start,months,end", [
    (date(2026, 7, 14), 6, date(2027, 1, 14)),
    (date(2026, 8, 31), 6, date(2027, 2, 28)),
    (date(2027, 8, 31), 6, date(2028, 2, 29)),
    (date(2026, 10, 14), 12, date(2027, 10, 14)),
])
def test_months_are_added_by_the_calendar(start, months, end):
    assert add_months(start, months) == end


def _get(client, headers, day="2026-10-14", rego="TMS041"):
    r = client.get(f"/v1/vehicles/{rego}", headers=headers, params={"date": day})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_truck_with_nothing_recorded_is_free_to_go(client, db, manager_headers):
    r = client.post("/v1/vehicles", headers=manager_headers, json={
        "rego": "TMS052", "type": "van", "max_kg": "1200", "max_m3": "9",
        "service_every_months": 6, "service_every_km": 15000})
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["service_every_months"], out["service_every_km"]) == (6, 15000)
    assert out["odometer_km"] is None and out["grounded"] is None
    assert out["last_prestart"] is None and out["blocked"] is None and out["state"] == "ok"
    assert out["rego_due"] == {"expires_on": None, "days_left": None, "state": "unknown"}
    assert out["service_due"]["state"] == "unknown"
    r = client.patch("/v1/vehicles/TMS052", headers=manager_headers, json={"service_every_km": 20000})
    assert r.json()["service_every_km"] == 20000 and r.json()["service_every_months"] == 6


def test_rego_is_amber_a_month_out_and_red_once_it_has_run_out(client, db, dispatcher_headers):
    v = vehicle(db)
    record(db, v, "rego", expires_on=date(2026, 10, 25))
    assert _get(client, dispatcher_headers, "2026-09-24")["rego_due"]["state"] == "ok"
    soon = _get(client, dispatcher_headers, "2026-10-13")
    assert soon["rego_due"] == {"expires_on": "2026-10-25", "days_left": 12, "state": "due"}
    assert soon["state"] == "due" and soon["blocked"] is None
    # good on the day it runs out, overdue the day after
    assert _get(client, dispatcher_headers, "2026-10-25")["rego_due"]["state"] == "due"
    late = _get(client, dispatcher_headers, "2026-10-26")
    assert late["state"] == "overdue"
    assert late["blocked"] == {"code": "vehicle_overdue", "message":
                               "TMS041's rego runs out on 25 Oct 2026. Record the new expiry "
                               "in Fleet first."}
    # a newer record puts it right; the old one stays
    record(db, v, "rego", expires_on=date(2027, 10, 25))
    assert _get(client, dispatcher_headers, "2026-10-26")["state"] == "ok"


def test_service_is_due_by_date_or_kilometres_whichever_comes_first(client, db, manager_headers):
    v = vehicle(db)
    client.patch("/v1/vehicles/TMS041", headers=manager_headers,
                 json={"service_every_months": 6, "service_every_km": 15000})
    serviced(db, v, date(2026, 4, 30), km=46900)
    check(db, v, km=50000)
    out = _get(client, manager_headers)
    assert out["odometer_km"] == 50000
    assert out["service_due"] == {"last_on": "2026-04-30", "last_km": 46900,
                                  "due_on": "2026-10-30", "due_km": 61900, "days_left": 16,
                                  "km_left": 11900, "state": "due"}
    check(db, v, km=61208)
    assert _get(client, manager_headers, "2026-09-01")["service_due"]["km_left"] == 692
    assert _get(client, manager_headers, "2026-09-01")["service_due"]["state"] == "due"
    check(db, v, km=62000)
    over = _get(client, manager_headers, "2026-09-01")
    assert over["state"] == "overdue" and over["blocked"]["message"] == (
        "TMS041's service was due at 61,900 km and it has done 62,000. "
        "Record the service in Fleet first.")
    assert _get(client, manager_headers, "2026-11-02")["blocked"]["message"] == (
        "TMS041's service was due by 30 Oct 2026. Record the service in Fleet first.")
    serviced(db, v, date(2026, 10, 14), km=62010)
    assert _get(client, manager_headers, "2026-11-02")["state"] == "ok"


def test_a_service_by_kilometres_alone_is_judged_by_them(client, db, manager_headers):
    v = vehicle(db)
    client.patch("/v1/vehicles/TMS041", headers=manager_headers,
                 json={"service_every_km": 15000})
    serviced(db, v, date(2026, 4, 30), km=60000)
    check(db, v, km=61000)
    out = _get(client, manager_headers)
    assert out["service_due"] == {"last_on": "2026-04-30", "last_km": 60000,
                                  "due_on": None, "due_km": 75000, "days_left": None,
                                  "km_left": 14000, "state": "ok"}


def test_the_odometer_only_goes_up_until_a_fleet_manager_puts_it_right(client, db,
                                                                      dispatcher_headers):
    v = vehicle(db)
    check(db, v, km=612080)                     # a slip of the finger
    serviced(db, v, date(2026, 10, 1), km=61000)  # recorded late: it can't take it back
    assert _get(client, dispatcher_headers)["odometer_km"] == 612080
    record(db, v, "odometer", odometer_km=61208, note="Typed one digit too many")
    assert _get(client, dispatcher_headers)["odometer_km"] == 61208
    check(db, v, km=61300)
    assert _get(client, dispatcher_headers)["odometer_km"] == 61300


def test_a_failed_critical_item_grounds_the_truck_until_it_is_marked_fixed(client, db,
                                                                         dispatcher_headers):
    v = vehicle(db)
    check(db, v, km=61000, failed=("Mirrors",))
    out = _get(client, dispatcher_headers)
    assert out["state"] == "ok" and out["last_prestart"]["result"] == "failed"
    bad = check(db, v, km=61010, failed=("Brakes", "Mirrors"))
    out = _get(client, dispatcher_headers)
    assert out["state"] == "grounded"
    assert out["grounded"] == {"check_id": bad.id, "at": out["grounded"]["at"], "failed": ["Brakes"]}
    assert out["blocked"] == {"code": "vehicle_grounded", "message":
                              "TMS041 is grounded: Brakes failed the pre-start. A fleet manager "
                              "has to mark it fixed first."}
    # a check that passes doesn't clear it: only a fleet manager does
    check(db, v, km=61020)
    assert _get(client, dispatcher_headers)["state"] == "grounded"
    record(db, v, "fixed", check_id=bad.id, note="New brake pads, tested")
    out = _get(client, dispatcher_headers)
    assert out["state"] == "ok" and out["grounded"] is None and out["blocked"] is None
