"""Sending a consignment with a carrier: booked on the carrier's manifest for
the day, its con note typed or scanned and put right, moved between carriers
and runs. On one run or with one carrier, never both, however many people
are at it."""
from datetime import timedelta

from carriers_samples import RIDGELINE, VALLEY, book, con_note, lines, new_carrier
from conftest import all_at_once, fresh_client
from runs_samples import DAY, assign, consignment, driver, new_run, order, vehicle


def _history(client, headers, cid) -> list[tuple[str, dict]]:
    h = client.get(f"/v1/consignments/{cid}", headers=headers).json()["history"]
    return [(e["kind"], e["detail"]) for e in h]


def _status(client, headers, cid) -> str:
    return client.get(f"/v1/consignments/{cid}", headers=headers).json()["status"]


def _bay(client, headers, day=DAY, **params) -> list[dict]:
    r = client.get("/v1/manifests", headers=headers, params={"date": day.isoformat(), **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_from_the_tray_onto_the_carriers_manifest_for_the_day(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers, account="RX-4410", pickup_time="15:30")
    c = consignment(db, customer="Mill Lane Florist", suburb="Daylesford", kg="24")
    r = book(client, dispatcher_headers, rx["id"], c.id)
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["manifest_no"], m["date"], m["status"], m["carrier"]["name"]) == (
        f"M-{m['id']:04d}", DAY.isoformat(), "open", RIDGELINE)
    assert m["carrier"]["pickup_time"] == "15:30"
    line = m["lines"][0]
    assert line["consignment"] == {"id": c.id, "wms_ref": c.wms_ref, "customer": "Mill Lane Florist",
                                   "suburb": "Daylesford", "status": "booked", "packages": 1,
                                   "cartons": 1, "weight_kg": "24", "volume_m3": "0.5"}
    assert line["con_note"] is None
    assert m["totals"] == {"consignments": 1, "packages": 1, "cartons": 1, "weight_kg": "24"}
    assert m["needs_con_note"] == 1 and m["print_job"] is None
    assert m["shipping"] == {"pending": 0, "done": 0, "failed": 0}
    assert _history(client, dispatcher_headers, c.id)[-1] == ("booked", {
        "carrier": RIDGELINE, "carrier_id": rx["id"], "manifest": m["manifest_no"],
        "manifest_id": m["id"], "by": "dee"})
    assert [x["id"] for x in _bay(client, dispatcher_headers)] == [m["id"]]


def test_a_con_note_with_the_booking_is_tidied_and_kept_in_the_history(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id, con_note="  rx-77310042 ").json()
    assert lines(m) == [(c.id, "RX-77310042")] and m["needs_con_note"] == 0
    assert _history(client, dispatcher_headers, c.id)[-1] == ("con_note", {
        "carrier": RIDGELINE, "carrier_id": rx["id"], "con_note": "RX-77310042", "was": None,
        "by": "dee"})


def test_a_con_note_is_letters_digits_and_dashes(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id).json()
    for bad in ("RX 7731", "RX", "-RX7731", "RX_7731", "R" * 41):
        r = con_note(client, dispatcher_headers, m["id"], c.id, bad)
        assert r.status_code == 422, bad
        assert r.json()["errors"] == [{"field": "con_note", "message":
                                       "A con note is 4 to 40 letters, digits and dashes, as on "
                                       "the carrier's label"}]


def test_putting_a_con_note_right_is_a_new_row_and_the_same_again_is_nothing(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310024").json()
    r = con_note(client, dispatcher_headers, m["id"], c.id, "RX-77310042")
    assert r.status_code == 200, r.text
    assert lines(r.json()) == [(c.id, "RX-77310042")]
    con_note(client, dispatcher_headers, m["id"], c.id, "rx-77310042")
    notes = [d for k, d in _history(client, dispatcher_headers, c.id) if k == "con_note"]
    assert [(d["con_note"], d["was"]) for d in notes] == [("RX-77310024", None),
                                                         ("RX-77310042", "RX-77310024")]


def test_a_con_note_is_one_consignments_with_a_carrier(
        client, db, manager_headers, dispatcher_headers):
    rx, vf = new_carrier(client, manager_headers), new_carrier(client, manager_headers, VALLEY)
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery")
    b = consignment(db, ref="0080034520")
    c = consignment(db, ref="0080034521")
    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    r = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "con_note", "message":
                                   f"RX-77310042 is already the con note of {a.wms_ref} "
                                   f"(Hilltop Joinery) with {RIDGELINE}"}]
    # refused whole: not booked either
    assert _status(client, dispatcher_headers, b.id) == "packed"
    # another carrier's numbers are its own
    assert book(client, dispatcher_headers, vf["id"], c.id, con_note="RX-77310042").status_code == 200


def test_a_con_note_stays_its_consignments_after_it_is_taken_off_the_bay(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery")
    b = consignment(db, ref="0080034520")
    m = book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042").json()
    client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                json={"consignment_id": a.id})
    r = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "con_note", "message":
                                   f"RX-77310042 is already the con note of {a.wms_ref} "
                                   f"(Hilltop Joinery) with {RIDGELINE}"}]
    m2 = book(client, dispatcher_headers, rx["id"], b.id).json()
    assert con_note(client, dispatcher_headers, m2["id"], b.id, "RX-77310042").status_code == 422
    # its own again: back on the bay, it may have it
    again = book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    assert again.status_code == 200, again.text


def test_a_con_note_put_right_is_free_again(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    a, b = consignment(db, ref="0080034519"), consignment(db, ref="0080034520")
    m = book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310024").json()
    con_note(client, dispatcher_headers, m["id"], a.id, "RX-77310042")
    assert book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310024").status_code == 200


def test_one_con_note_given_to_two_at_once_goes_to_one(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    ids = [consignment(db, ref=f"008003452{i}").id for i in range(4)]
    for i in ids:
        book(client, dispatcher_headers, rx["id"], i)
    m = _bay(client, dispatcher_headers)[0]
    counter = iter(ids)

    def go():
        cid = next(counter)
        with fresh_client() as other:
            return con_note(other, dispatcher_headers, m["id"], cid, "RX-77310042").status_code

    assert sorted(all_at_once(4, go)) == [200, 422, 422, 422]


def test_the_same_booking_twice_changes_nothing(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042")
    again = book(client, dispatcher_headers, rx["id"], c.id)
    assert lines(again.json()) == [(c.id, "RX-77310042")]
    assert [k for k, _ in _history(client, dispatcher_headers, c.id)] == ["packed", "booked",
                                                                          "con_note"]


def test_to_another_carrier_without_its_con_note(client, db, manager_headers, dispatcher_headers):
    rx, vf = new_carrier(client, manager_headers), new_carrier(client, manager_headers, VALLEY)
    c = consignment(db)
    first = book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042").json()
    m = book(client, dispatcher_headers, vf["id"], c.id).json()
    assert m["carrier"]["name"] == VALLEY and lines(m) == [(c.id, None)]
    assert [x["carrier"]["name"] for x in _bay(client, dispatcher_headers)] == [VALLEY]
    assert _history(client, dispatcher_headers, c.id)[-2:] == [
        ("unbooked", {"carrier": RIDGELINE, "manifest": first["manifest_no"],
                      "manifest_id": first["id"], "by": "dee"}),
        ("booked", {"carrier": VALLEY, "carrier_id": vf["id"], "manifest": m["manifest_no"],
                    "manifest_id": m["id"], "by": "dee"})]
    # its old number stays its own with that carrier, for good
    d = consignment(db, ref="0080034599")
    assert book(client, dispatcher_headers, rx["id"], d.id, con_note="RX-77310042").status_code == 422


def test_to_another_day_with_the_same_carrier_keeps_its_con_note(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042")
    m = book(client, dispatcher_headers, rx["id"], c.id, day=DAY + timedelta(days=1)).json()
    assert m["date"] == (DAY + timedelta(days=1)).isoformat()
    assert lines(m) == [(c.id, "RX-77310042")]


def test_off_a_planned_run_onto_a_carrier_and_back_onto_a_run(
        client, db, manager_headers, dispatcher_headers):
    vehicle(db)
    driver(db)
    rx = new_carrier(client, manager_headers)
    run = new_run(client, dispatcher_headers)
    a, b = consignment(db, ref="0080034519"), consignment(db, ref="0080034520")
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    assert book(client, dispatcher_headers, rx["id"], a.id).status_code == 200
    now = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert order(now) == [b.id] and now["stops"][0]["sequence"] == 1
    assert [k for k, _ in _history(client, dispatcher_headers, a.id)][-2:] == ["unassigned", "booked"]

    r = assign(client, dispatcher_headers, run["id"], a.id)
    assert r.status_code == 200, r.text
    assert order(r.json()) == [b.id, a.id]
    assert _status(client, dispatcher_headers, a.id) == "assigned"
    assert [k for k, _ in _history(client, dispatcher_headers, a.id)][-2:] == ["unbooked", "assigned"]
    assert _bay(client, dispatcher_headers) == []


def test_what_has_left_cant_go_with_a_carrier(client, db, manager_headers, dispatcher_headers):
    vehicle(db)
    driver(db)
    rx = new_carrier(client, manager_headers)
    run = new_run(client, dispatcher_headers)
    c = consignment(db)
    assign(client, dispatcher_headers, run["id"], c.id)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    r = book(client, dispatcher_headers, rx["id"], c.id)
    assert r.status_code == 409 and r.json()["code"] == "not_plannable"
    assert r.json()["detail"] == "Station Deli (0080034519) has left on its run, so it can't go with a carrier"


def test_nothing_new_with_a_deactivated_carrier(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    client.post(f"/v1/carriers/{rx['id']}/deactivate", headers=manager_headers)
    r = book(client, dispatcher_headers, rx["id"], consignment(db).id)
    assert r.status_code == 409 and r.json()["code"] == "carrier_inactive"
    assert r.json()["detail"] == f"{RIDGELINE} is deactivated, so nothing more can be booked with it"
    assert book(client, dispatcher_headers, 99, 1).status_code == 404


def test_back_to_the_tray(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042").json()
    r = client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                    json={"consignment_id": c.id})
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == []
    assert _status(client, dispatcher_headers, c.id) == "packed"
    assert _bay(client, dispatcher_headers) == []
    again = client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                        json={"consignment_id": c.id})
    assert again.status_code == 200
    assert [k for k, _ in _history(client, dispatcher_headers, c.id)] == [
        "packed", "booked", "con_note", "unbooked"]
    r = con_note(client, dispatcher_headers, m["id"], c.id, "RX-77310043")
    assert r.status_code == 404
    assert r.json()["detail"] == f"{m['manifest_no']} has no consignment {c.id}"


def test_the_bay_today_and_what_is_still_waiting_from_before(
        client, db, manager_headers, dispatcher_headers):
    rx, vf = new_carrier(client, manager_headers), new_carrier(client, manager_headers, VALLEY)
    yesterday = DAY - timedelta(days=1)
    old = book(client, dispatcher_headers, rx["id"], consignment(db, ref="0080034501").id,
               day=yesterday).json()
    today = book(client, dispatcher_headers, vf["id"], consignment(db, ref="0080034502").id).json()
    assert [m["id"] for m in _bay(client, dispatcher_headers)] == [today["id"]]
    assert [m["id"] for m in _bay(client, dispatcher_headers, left_open=True)] == [old["id"], today["id"]]
    got = client.get(f"/v1/manifests/{old['id']}", headers=dispatcher_headers)
    assert got.status_code == 200 and got.json()["date"] == yesterday.isoformat()


def test_on_a_run_or_with_a_carrier_never_both_however_many_at_once(
        client, db, manager_headers, dispatcher_headers):
    vehicle(db)
    driver(db)
    rx = new_carrier(client, manager_headers)
    run = new_run(client, dispatcher_headers)
    c = consignment(db)

    def go(i):
        with fresh_client() as other:
            if i % 2:
                return book(other, dispatcher_headers, rx["id"], c.id).status_code
            return assign(other, dispatcher_headers, run["id"], c.id).status_code

    counter = iter(range(6))
    assert all_at_once(6, lambda: go(next(counter))) == [200] * 6
    on_run = len(client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["stops"])
    on_bay = sum(len(m["lines"]) for m in _bay(client, dispatcher_headers))
    assert on_run + on_bay == 1


def test_a_driver_cant_book(client, db, manager_headers):
    from deliveries_samples import driver_headers
    driver(db)
    rx = new_carrier(client, manager_headers)
    assert book(client, driver_headers(client), rx["id"], consignment(db).id).status_code == 403
