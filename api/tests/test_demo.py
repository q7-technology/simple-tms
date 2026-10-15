"""The demo day: made through the API, from Simple WMS freight, and only
into an empty database."""
import json
import struct
import uuid
import zlib
from datetime import UTC, datetime

import httpx
import pytest

from tms import demo, demo_pictures

# 01:30 UTC is 12:30 pm in Ballarat: the same day by both clocks
AT = datetime.now(UTC).replace(hour=1, minute=30, second=0, microsecond=0)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    zlib.decompress(data[data.index(b"IDAT") + 4:])  # the pixels read back
    return width, height


# --- the pictures ----------------------------------------------------------------

def test_a_signature_is_a_small_png_and_each_is_its_own():
    one, two = demo_pictures.signature(1), demo_pictures.signature(2)
    assert png_size(one) == (480, 160)
    assert len(one) < 1_000_000
    assert one != two
    assert demo_pictures.signature(1) == one


def test_photos_are_pngs_under_the_photo_limit():
    for picture in (demo_pictures.parcel_photo(3), demo_pictures.tail_light_photo(3)):
        assert png_size(picture) == (640, 480)
        assert len(picture) < 5_000_000
    assert demo_pictures.parcel_photo(3) != demo_pictures.parcel_photo(4)


# --- Simple WMS, through its API --------------------------------------------------

def wms_over(handler) -> demo.Wms:
    return demo.Wms("http://wms.test", "wms_k.secret", warehouse="BAL-WH01", operator="op-017",
                    skus=[("JKL012", "EA"), ("DEF456", "EA")],
                    client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_a_delivery_is_created_picked_and_packed_carton_by_carton():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body, request.headers["Authorization"]))
        if request.url.path == "/v1/deliveries":
            return httpx.Response(202, json={"status": "accepted", "allocation": [
                {"delivery_line": 10, "sku": "DEF456", "short": "0"}]})
        if request.url.path == "/v1/tasks":
            return httpx.Response(200, json={"items": [{"wms_id": "41", "lines": [
                {"line_no": 1, "source_line": 10, "sku": "DEF456", "batch": "B2601",
                 "expected_qty": "2", "uom": "EA"},
                {"line_no": 2, "source_line": 20, "sku": "JKL012", "batch": None,
                 "expected_qty": "3", "uom": "EA"}]}]})
        return httpx.Response(202, json={"status": "accepted"})

    drop = demo.ROAD[0]   # two cartons: a small carton and a satchel
    wms_over(handler).pack("0081234501", drop, AT.date(), 0)

    assert [(m, p) for m, p, _, _ in calls] == [
        ("POST", "/v1/deliveries"), ("GET", "/v1/tasks"), ("POST", "/v1/tasks/41/start"),
        ("POST", "/v1/tasks/41/lines/1/confirm"), ("POST", "/v1/tasks/41/lines/2/confirm"),
        ("POST", "/v1/deliveries/0081234501/pack")]
    assert {auth for *_, auth in calls} == {"Bearer wms_k.secret"}
    created = calls[0][2]
    assert created["ship_to"]["name"] == "Gold Hill Pharmacy"
    assert created["ship_to"]["phone"].startswith("03 5550 ")
    assert [line["sku"] for line in created["lines"]] == ["DEF456", "JKL012"]
    assert all(uuid.UUID(body["message_id"]) for _, _, body, _ in calls if body)
    confirmed = calls[3][2]
    assert (confirmed["qty"], confirmed["batch"], confirmed["operator"]) == ("2", "B2601", "op-017")
    packages = calls[-1][2]["packages"]
    assert [(p["package_no"], p["type"], p["weight_kg"]) for p in packages] == [
        (1, "carton", "6.5"), (2, "satchel", "1.2")]
    # each carton carries what was picked for its line, batch and all
    assert packages[0]["lines"] == [{"delivery_line": 10, "sku": "DEF456", "batch": "B2601",
                                     "qty": "2", "uom": "EA"}]
    assert packages[1]["lines"] == [{"delivery_line": 20, "sku": "JKL012", "batch": None,
                                     "qty": "3", "uom": "EA"}]


def test_a_delivery_simple_wms_cant_fill_stops_the_seed():
    def handler(request):
        return httpx.Response(202, json={"allocation": [
            {"delivery_line": 10, "sku": "JKL012", "short": "2"}]})

    with pytest.raises(demo.SeedError, match=r"JKL012 \(2 short\).*--skus"):
        wms_over(handler).pack("0081234501", demo.TRAY[1], AT.date(), 0)


def test_simple_wms_refusing_says_what_it_said():
    def handler(request):
        return httpx.Response(403, json={"detail": "you do not have the tasks:write scope"})

    with pytest.raises(demo.SeedError, match="403.*tasks:write"):
        wms_over(handler).pack("0081234501", demo.TRAY[1], AT.date(), 0)


def test_no_simple_wms_set_up_is_said_plainly():
    with pytest.raises(demo.SeedError, match="TMS_WMS_URL and TMS_WMS_API_KEY"):
        demo.Wms("", "", warehouse="BAL-WH01", operator="op-017", skus=[("JKL012", "EA")])
