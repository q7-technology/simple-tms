from conftest import WEBHOOK_SECRET
from wms_samples import delivery, packed_event, post_event


def arrive(client, wms, ref: str, customer: str, event: dict | None = None):
    d = delivery(ref)
    d["ship_to"] = {**d["ship_to"], "name": customer}
    wms.deliveries[ref] = d
    r = post_event(client, event or packed_event(ref), WEBHOOK_SECRET)
    assert r.status_code == 200, r.text
    return r.json()["consignment_id"]


def test_the_list_is_newest_first_with_packages(client, wms, dispatcher_headers):
    arrive(client, wms, "2044", "Ballarat Hardware")
    arrive(client, wms, "2045", "Sebastopol Farm Supplies")
    r = client.get("/v1/consignments", headers=dispatcher_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert [c["wms_ref"] for c in body["items"]] == ["2045", "2044"]
    first = body["items"][0]
    assert first["customer"] == "Sebastopol Farm Supplies"
    assert first["status"] == "packed"
    assert first["packages"][0] == {"package_no": 1, "type": "carton", "weight_kg": "9.5",
                                    "length_cm": "60", "width_cm": "40", "height_cm": "40",
                                    "sscc": "093123450000000012"}
    assert first["packages"][1]["weight_kg"] is None
    assert "history" not in first


def test_decimals_come_back_exactly_as_strings(client, wms, dispatcher_headers):
    event = packed_event("2044")
    event["data"]["packages"][0]["weight_kg"] = "123456789012.123456"
    event["data"]["packages"][0]["height_cm"] = "0.000001"
    cid = arrive(client, wms, "2044", "Ballarat Hardware", event)
    p = client.get(f"/v1/consignments/{cid}", headers=dispatcher_headers).json()["packages"][0]
    assert p["weight_kg"] == "123456789012.123456"
    assert p["height_cm"] == "0.000001"


def test_search_by_delivery_or_customer(client, wms, dispatcher_headers):
    arrive(client, wms, "2044", "Ballarat Hardware")
    arrive(client, wms, "2045", "Sebastopol Farm Supplies")
    by_ref = client.get("/v1/consignments?q=2044", headers=dispatcher_headers).json()
    assert [c["wms_ref"] for c in by_ref["items"]] == ["2044"]
    by_name = client.get("/v1/consignments?q=sebastopol", headers=dispatcher_headers).json()
    assert [c["wms_ref"] for c in by_name["items"]] == ["2045"]
    by_status = client.get("/v1/consignments?status=delivered", headers=dispatcher_headers).json()
    assert by_status["total"] == 0
    by_warehouse = client.get("/v1/consignments?warehouse=BAL-WH01", headers=dispatcher_headers)
    assert by_warehouse.json()["total"] == 2


def test_search_text_is_text_not_a_pattern(client, wms, dispatcher_headers):
    arrive(client, wms, "2044", "Ballarat Hardware")
    for q in ("%", "_"):
        r = client.get("/v1/consignments", params={"q": q}, headers=dispatcher_headers)
        assert r.json()["total"] == 0, q


def test_paging(client, wms, dispatcher_headers):
    arrive(client, wms, "2044", "Ballarat Hardware")
    arrive(client, wms, "2045", "Sebastopol Farm Supplies")
    page = client.get("/v1/consignments?limit=1&offset=1", headers=dispatcher_headers).json()
    assert page["total"] == 2 and [c["wms_ref"] for c in page["items"]] == ["2044"]
    for bad in ("limit=-1", "offset=-1", "limit=2001"):
        assert client.get(f"/v1/consignments?{bad}", headers=dispatcher_headers).status_code == 422


def test_one_consignment_with_its_history(client, wms, dispatcher_headers):
    cid = arrive(client, wms, "2044", "Ballarat Hardware")
    r = client.get(f"/v1/consignments/{cid}", headers=dispatcher_headers)
    assert r.status_code == 200
    c = r.json()
    assert c["ship_to"]["suburb"] == "Ballarat"
    assert c["required_by"] == "2026-10-11"
    assert [h["kind"] for h in c["history"]] == ["packed"]
    assert c["history"][0]["at"].startswith("2026-10-10T03:12:00")
    assert c["history"][0]["detail"]["packages"] == 2


def test_an_unknown_consignment_is_404(client, dispatcher_headers):
    assert client.get("/v1/consignments/999", headers=dispatcher_headers).status_code == 404


def test_consignments_need_a_session(client):
    assert client.get("/v1/consignments").status_code == 401
    assert client.get("/v1/consignments/1").status_code == 401


def test_a_driver_cannot_list_consignments_yet(client, manager_headers):
    client.post("/v1/drivers", headers=manager_headers,
                json={"code": "drv-01", "name": "Alex T.", "pin": "2468"})
    token = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"}).json()["token"]
    r = client.get("/v1/consignments", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
