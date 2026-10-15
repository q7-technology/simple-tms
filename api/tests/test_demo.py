"""The demo day: made through the API, from Simple WMS freight, and only
into an empty database."""
import json
import struct
import uuid
import zlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from conftest import WEBHOOK_SECRET, fresh_client
from tms import demo, demo_pictures
from tms.cli import main
from tms.models import Consignment, OutboundCall, PodFile, PrintJob, Vehicle
from wms_samples import post_event

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


def test_a_delivery_is_created_then_picked_and_packed_carton_by_carton():
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
    wms = wms_over(handler)
    assert wms.create("DEMO-261015-123000-01", drop, AT.date(), 0) == []
    assert len(calls) == 1
    wms.pick_and_pack("DEMO-261015-123000-01", drop, 0)

    assert [(m, p) for m, p, _, _ in calls] == [
        ("POST", "/v1/deliveries"), ("GET", "/v1/tasks"), ("POST", "/v1/tasks/41/start"),
        ("POST", "/v1/tasks/41/lines/1/confirm"), ("POST", "/v1/tasks/41/lines/2/confirm"),
        ("POST", "/v1/deliveries/DEMO-261015-123000-01/pack")]
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


def stocked(stock: dict[str, int], skus: list[str]):
    """A Simple WMS with this much on the shelf: creating a delivery reserves
    what there is and says what is short, as its allocation does; cancelling
    puts the reservation back; an unknown SKU is a 422 and nothing made."""
    shelf, held, calls = dict(stock), {}, []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/deliveries":
            for i, line in enumerate(body["lines"]):
                if line["sku"] not in shelf:
                    return httpx.Response(422, json={"errors": [
                        {"field": f"lines.{i}.sku", "message": f"unknown sku {line['sku']}"}]})
            rows, held[body["external_ref"]] = [], []
            for line in body["lines"]:
                take = min(line["qty"], shelf[line["sku"]])
                shelf[line["sku"]] -= take
                held[body["external_ref"]].append((line["sku"], take))
                rows.append({"delivery_line": line["delivery_line"], "sku": line["sku"],
                             "qty_ordered": str(line["qty"]), "qty_allocated": str(take),
                             "uom": line["uom"], "short": str(line["qty"] - take)})
            return httpx.Response(202, json={"status": "accepted", "allocation": rows})
        if request.url.path.endswith("/cancel"):
            for sku, take in held.pop(request.url.path.split("/")[3]):
                shelf[sku] += take
            return httpx.Response(202, json={"status": "accepted"})
        return httpx.Response(500, json={"detail": f"not in this test: {request.url.path}"})

    wms = demo.Wms("http://wms.test", "wms_k.secret", warehouse="BAL-WH01", operator="op-017",
                   skus=[(sku, "EA") for sku in skus],
                   client=httpx.Client(transport=httpx.MockTransport(handler)))
    return wms, shelf, held, calls


def nothing_made_in_simple_tms(db):
    from tms.models import User

    assert db.execute(select(func.count()).select_from(User)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(Vehicle)).scalar_one() == 0


def test_a_later_delivery_short_stops_the_seed_before_anything_is_picked(db, quick):
    stock = {"JKL012": 1000, "DEF456": 12}
    wms, shelf, held, calls = stocked(stock, ["JKL012", "DEF456"])

    with pytest.raises(demo.SeedError) as e:
        demo.seed_demo(wms, at=AT, **quick)

    created = [p for m, p in calls if p == "/v1/deliveries"]
    cancelled = [p for m, p in calls if p.endswith("/cancel")]
    assert len(created) > 3                      # a later one was short
    assert len(cancelled) == len(created)        # every one made is cancelled
    assert not [p for m, p in calls if p.startswith("/v1/tasks")]   # nothing picked
    assert held == {} and shelf == stock         # nothing left reserved
    message = str(e.value)
    assert "DEF456 is " in message and " short" in message and "--skus" in message
    assert f"cancelled the {len(created)} deliveries it made" in message
    nothing_made_in_simple_tms(db)


def test_an_unknown_sku_stops_the_seed_and_cancels_what_it_made(db, quick):
    # the fourth delivery is the first with the first SKU
    wms, shelf, held, calls = stocked({"JKL012": 1000, "DEF456": 1000, "ABC123": 1000},
                                      ["NOPE-1", "JKL012", "DEF456", "ABC123"])

    with pytest.raises(demo.SeedError, match="unknown sku NOPE-1.*--skus") as e:
        demo.seed_demo(wms, at=AT, **quick)

    assert len([p for m, p in calls if p == "/v1/deliveries"]) == 4
    assert len([p for m, p in calls if p.endswith("/cancel")]) == 3
    assert held == {}
    assert "cancelled the 3 deliveries it made" in str(e.value)
    nothing_made_in_simple_tms(db)


def test_simple_wms_refusing_says_what_it_said_but_never_the_key():
    def handler(request):
        # a proxy that echoes what it was sent, key and all
        return httpx.Response(403, json={"detail": "you do not have the tasks:write scope",
                                         "sent": request.headers["Authorization"]})

    with pytest.raises(demo.SeedError, match="403.*tasks:write") as e:
        wms_over(handler).create("DEMO-261015-123000-01", demo.TRAY[1], AT.date(), 0)
    assert "wms_k.secret" not in str(e.value)
    assert "Bearer ***" in str(e.value)


def test_simple_wms_not_answering_says_so_but_never_the_key():
    # the real class, its own client, and nothing listening
    wms = demo.Wms("http://127.0.0.1:9", "wms_k.secret", warehouse="BAL-WH01", operator="op-017",
                   skus=[("JKL012", "EA")])
    with pytest.raises(demo.SeedError, match="Simple WMS at http://127.0.0.1:9 didn't answer") as e:
        wms.create("DEMO-261015-123000-01", demo.TRAY[1], AT.date(), 0)
    assert "wms_k.secret" not in str(e.value)


def test_no_simple_wms_set_up_is_said_plainly():
    with pytest.raises(demo.SeedError, match="TMS_WMS_URL and TMS_WMS_API_KEY"):
        demo.Wms("", "", warehouse="BAL-WH01", operator="op-017", skus=[("JKL012", "EA")])


# --- the day ---------------------------------------------------------------------

class FakeWms:
    """Packs as Simple WMS would, then sends delivery.packed, signed, as its
    worker does; and answers Simple TMS's read of the delivery (the wms fixture)."""

    warehouse = "BAL-WH01"

    def __init__(self, wms):
        self.wms, self.client, self.created, self.packed = wms, fresh_client(), {}, []

    def create(self, ref, drop, required_by, n):
        self.created[ref] = required_by
        return []

    def cancel(self, ref):
        raise AssertionError(f"{ref} cancelled")

    def pick_and_pack(self, ref, drop, n):
        required_by = self.created[ref]
        self.packed.append(ref)
        self.wms.deliveries[ref] = {
            "wms_id": str(n), "external_ref": ref, "owner": "DEFAULT", "warehouse": "BAL-WH01",
            "status": "packed", "required_by": required_by.isoformat(),
            "ship_to": {"name": drop.customer, "address": drop.address, "suburb": drop.suburb,
                        "state": "VIC", "postcode": drop.postcode, "country": "AU",
                        "contact": drop.contact, "phone": f"03 5550 {1000 + n:04d}"},
            "lines": [], "packages": []}
        event = {"event_id": str(uuid.uuid4()), "event_type": "delivery.packed",
                 "occurred_at": "2026-10-15T01:00:00Z", "warehouse": "BAL-WH01",
                 "owner": "DEFAULT", "external_ref": ref, "data": {"delivery_ref": ref, "packages": [
                     {"package_no": i, "type": kind, "weight_kg": kg, "length_cm": dims[0],
                      "width_cm": dims[1], "height_cm": dims[2], "sscc": None, "container_id": None,
                      "contents": []} for i, (kind, kg, dims) in enumerate(drop.cartons, 1)]}}
        r = post_event(self.client, event, WEBHOOK_SECRET)
        assert r.json()["status"] == "created", r.text


@pytest.fixture
def quick(monkeypatch):
    """No waiting: positions may come as fast as the seed sends them."""
    from tms.services import location

    monkeypatch.setattr(location, "MIN_GAP", timedelta(0))
    return {"sleep": lambda s: None, "pace_minutes": 0, "wait_seconds": 5, "position_gap": 0}


@pytest.fixture
def printing(monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "platen_url", "http://127.0.0.1:9/intake/jobs")


def seeded(wms, quick, client):
    made = demo.seed_demo(FakeWms(wms), at=AT, **quick)
    token = client.post("/v1/auth/login", json={"username": "dee", "password": demo.PASSWORD}).json()
    return made, {"Authorization": f"Bearer {token['token']}"}


def test_the_demo_day_is_a_depot_partway_through_its_day(db, client, wms, quick, printing):
    made, h = seeded(wms, quick, client)
    day = made.day.isoformat()

    statuses = dict(db.execute(select(Consignment.status, func.count())
                               .group_by(Consignment.status)).all())
    assert statuses == {"delivered": 5, "failed": 1, "departed": 3, "assigned": 3,
                        "collected": 2, "booked": 3, "packed": 2}

    runs = {r["run_no"]: r for r in client.get(f"/v1/runs?date={day}", headers=h).json()["items"]}
    assert {n: (r["status"], r["vehicle"]["rego"], r["driver"]["code"], len(r["stops"]))
            for n, r in runs.items()} == {
        "R-0001": ("done", "TMS-033", "tom", 4), "R-0002": ("departed", "TMS-041", "kim", 5),
        "R-0003": ("planned", "TMS-052", "ana", 3), "R-0004": ("planned", "TMS-027", "raj", 0)}
    assert [s["outcome"] for s in runs["R-0001"]["stops"]] == ["delivered"] * 3 + ["failed"]
    assert runs["R-0001"]["stops"][3]["reason"] == "closed"
    assert [s["outcome"] for s in runs["R-0002"]["stops"]] == ["delivered"] * 2 + [None] * 3
    assert runs["R-0002"]["estimate"]["basis"] == "average"
    assert runs["R-0003"]["load"]["state"] == "near"
    assert runs["R-0004"]["vehicle"]["blocked"]["code"] == "vehicle_grounded"

    trucks = {v["rego"]: v["state"] for v in
              client.get(f"/v1/vehicles?date={day}", headers=h).json()["items"]}
    assert trucks == {"TMS-027": "grounded", "TMS-033": "ok", "TMS-041": "ok", "TMS-052": "due"}
    assert made.vehicles == trucks

    where = client.get(f"/v1/positions?date={day}", headers=h).json()["items"]
    assert [(p["run_no"], p["latitude"]) for p in where] == [("R-0002", "-37.5931")]

    manifests = client.get(f"/v1/manifests?date={day}", headers=h).json()["items"]
    assert [(m["carrier"]["name"], m["status"], m["totals"]["consignments"], m["needs_con_note"],
             m["print_job"]["status"]) for m in manifests] == [
        ("Valley Freight (sample)", "collected", 2, 0, "pending"),
        ("Ridgeline Express (sample)", "open", 3, 0, "pending")]

    jobs = db.execute(select(PrintJob.template, func.count()).group_by(PrintJob.template)).all()
    assert dict(jobs) == {"run-sheet": 3, "carrier-manifest": 2}

    kinds = {i["kind"] for i in client.get("/v1/feed?limit=100", headers=h).json()["items"]}
    assert kinds >= {"packed", "left", "delivered", "failed", "finished", "con_note",
                     "collected", "prestart"}

    pictures = dict(db.execute(select(PodFile.kind, func.count()).group_by(PodFile.kind)).all())
    # signatures: Nina, Owen, Priya, Callum; photos: 2 + 1 on the morning run, 1 today, the tail light
    assert pictures == {"signature": 4, "photo": 5}

    tray = client.get("/v1/consignments?status=packed,failed", headers=h).json()["items"]
    assert sorted(c["customer"] for c in tray) == [
        "Alfredton Dental Studio", "Ironbark Cafe", "Learmonth Feed and Grain"]
    assert (made.tray, made.came_back, made.printed) == (3, 1, True)

    # the history says who did what
    found = client.get("/v1/consignments?q=Gold Hill", headers=h).json()["items"][0]
    history = client.get(f"/v1/consignments/{found['id']}", headers=h).json()["history"]
    assert [(e["kind"], e["detail"].get("by")) for e in history] == [
        ("packed", None), ("assigned", "dee"), ("departed", "kim"), ("delivered", "kim")]
    assert history[-1]["detail"]["signed_name"] == "Priya"

    # what came back is off the truck, and Simple WMS is to be told: the ship
    # calls of the two runs that left and the Valley pickup, and Ironbark's return
    calls = dict(db.execute(select(OutboundCall.kind, func.count())
                            .group_by(OutboundCall.kind)).all())
    assert calls == {"ship": 11, "return": 1}
    found = client.get("/v1/consignments?q=Ironbark", headers=h).json()["items"][0]
    history = client.get(f"/v1/consignments/{found['id']}", headers=h).json()["history"]
    assert [(e["kind"], e["detail"].get("by")) for e in history][-2:] == [
        ("failed", "tom"), ("returned", "tom")]

    # the summary says what was made, and no key
    lines = demo.summary(made)
    text = "\n".join(lines)
    assert "fleet: 4 trucks (TMS-027 grounded, TMS-033 ok, TMS-041 ok, TMS-052 due)" in text
    assert "R-0001 TMS-033 Tom: finished, 3 delivered, 1 came back" in lines
    assert "R-0002 TMS-041 Kim: on the road, 2 of 5 delivered" in lines
    assert "R-0003 TMS-052 Ana: planned, 3 stops, load near" in lines
    assert lines[6].startswith("R-0004 TMS-027 Raj: planned, no stops: TMS-027 is grounded")
    assert "M-0001 Valley Freight (sample): collected, 2 with con notes" in lines
    assert "M-0002 Ridgeline Express (sample): on the carrier bay, 3 with con notes" in lines
    assert "tray: 3 to plan (1 came back)" in lines
    assert "wms_test-key-for-the-tms" not in text and "tms_k." not in text


def test_without_printing_nothing_is_printed_and_it_says_so(db, client, wms, quick):
    made, _ = seeded(wms, quick, client)
    assert made.printed is False
    assert db.execute(select(func.count()).select_from(PrintJob)).scalar_one() == 0
    assert "nothing printed: printing isn't set up (TMS_PLATEN_URL)" in demo.summary(made)


def test_a_database_with_anything_in_it_is_refused(db, client, wms, quick):
    db.add(Vehicle(rego="ABC123", type="van", max_kg=1, max_m3=1, active=True))
    db.commit()
    fake = FakeWms(wms)
    with pytest.raises(demo.SeedError, match="already here: vehicles"):
        demo.seed_demo(fake, at=AT, **quick)
    assert fake.created == {} and fake.packed == []


def test_a_second_demo_day_is_refused(db, client, wms, quick):
    demo.seed_demo(FakeWms(wms), at=AT, **quick)
    with pytest.raises(demo.SeedError, match="already here: vehicles, drivers, carriers, runs, "
                                             "consignments, user dee, user sam"):
        demo.seed_demo(FakeWms(wms), at=AT, **quick)


def test_freight_that_never_arrives_is_said_plainly(db, client, wms, quick):
    class Silent:
        warehouse = "BAL-WH01"

        def create(self, *args):
            return []

        def pick_and_pack(self, *args):
            pass

    with pytest.raises(demo.SeedError, match="Simple WMS packed 19 deliveries but 0 reached"):
        demo.seed_demo(Silent(), at=AT, **{**quick, "wait_seconds": 0})


def test_refs_are_plainly_the_demo_and_new_each_second():
    one = demo.refs_for(AT)
    assert len(one) == 19 and len(set(one)) == 19
    # 01:30 UTC is 12:30 pm in Ballarat; never an SAP delivery number
    stamp = AT.astimezone(demo.DEPOT_TZ).strftime("%y%m%d-%H%M%S")
    assert one[0] == f"DEMO-{stamp}-01" and one[-1] == f"DEMO-{stamp}-19"
    for later in (timedelta(seconds=1), timedelta(minutes=1), timedelta(days=1), timedelta(days=100)):
        assert not set(one) & set(demo.refs_for(AT + later))


# --- the command ---------------------------------------------------------------------

def test_the_command_makes_the_day_and_prints_no_key(db, client, wms, quick, monkeypatch, capsys):
    made_with = {}

    def fake_wms(url, key, **kw):
        made_with.update(url=url, key=key, **kw)
        return FakeWms(wms)

    monkeypatch.setattr(demo, "Wms", fake_wms)
    real = demo.seed_demo
    monkeypatch.setattr(demo, "seed_demo", lambda w, **kw: real(w, at=AT, **{**kw, **quick}))
    assert main(["seed-demo", "--yes", "--skus", "JKL012,LUB-05:KG"]) == 0
    out = capsys.readouterr().out
    assert "R-0002 TMS-041 Kim: on the road, 2 of 5 delivered" in out
    assert "driver app: tom, kim, ana or raj with PIN 2468" in out
    assert "wms_test-key-for-the-tms" not in out
    assert out.rstrip().splitlines()[-1] == ("These demo sign-ins are public: deactivate dee, sam "
                                             "and the drivers before real use.")
    assert made_with["key"] == "wms_test-key-for-the-tms"
    assert made_with["skus"] == [("JKL012", "EA"), ("LUB-05", "KG")]
    assert made_with["warehouse"] == "BAL-WH01" and made_with["operator"] == "op-017"


def test_the_command_refuses_a_database_in_use(db, admin, capsys):
    db.add(Vehicle(rego="ABC123", type="van", max_kg=1, max_m3=1, active=True))
    db.commit()
    assert main(["seed-demo", "--yes"]) == 1
    said = capsys.readouterr()
    assert "seed-demo: already here: vehicles" in said.err
    assert "wms_test-key-for-the-tms" not in said.out + said.err


def test_the_command_with_simple_wms_away_says_so_and_never_the_key(db, capsys):
    # the real Wms, at the test settings' address, where nothing listens
    assert main(["seed-demo", "--yes"]) == 1
    said = capsys.readouterr()
    assert "seed-demo: Simple WMS at http://127.0.0.1:9 didn't answer" in said.err
    assert "Nothing was made in Simple WMS or Simple TMS." in said.err
    assert "part of a demo day" not in said.err
    assert "wms_test-key-for-the-tms" not in said.out + said.err


def test_the_command_says_what_it_will_do_and_does_nothing_without_yes(db, monkeypatch, capsys):
    from tms.config import get_settings
    from tms.models import User

    def no_wms(*args, **kw):
        raise AssertionError("Simple WMS touched")

    monkeypatch.setattr(demo, "Wms", no_wms)
    monkeypatch.setattr(demo, "seed_demo", no_wms)
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.example:8011")
    assert main(["seed-demo", "--warehouse", "MEL-WH02"]) == 1
    said = capsys.readouterr()
    assert said.out.splitlines()[1:] == [
        "- uses Simple WMS at http://wms.example:8011, warehouse MEL-WH02",
        "- creates and packs 19 deliveries; the Simple TMS worker then ships 11 of them out of "
        "that warehouse's stock and returns 1",
        "- prints run sheets and manifests on dispatch if Platen is set up",
        "- adds sign-ins dee and sam (password demo-password-1) and four drivers (PIN 2468), "
        "which are public in the README"]
    assert "--yes" in said.err
    assert "wms_test-key-for-the-tms" not in said.out + said.err
    assert db.execute(select(func.count()).select_from(User)).scalar_one() == 0


def test_the_command_names_each_printer(db, monkeypatch, capsys):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "run_sheet_printer", "office")
    monkeypatch.setattr(get_settings(), "manifest_printer", "bay")
    assert main(["seed-demo"]) == 1
    assert ("- prints run sheets on office and manifests on bay if Platen is set up"
            in capsys.readouterr().out)



def run_command(monkeypatch, wms_for_it, quick, *args):
    monkeypatch.setattr(demo, "Wms", lambda url, key, **kw: wms_for_it)
    real = demo.seed_demo
    monkeypatch.setattr(demo, "seed_demo", lambda w, **kw: real(w, **{**kw, "at": AT, **quick}))
    return main(["seed-demo", "--yes", *args])


def test_a_day_stopped_partway_says_it_is_half_made(db, client, wms, quick, monkeypatch, capsys):
    class Silent(FakeWms):
        def pick_and_pack(self, ref, drop, n):
            self.packed.append(ref)     # packed, but delivery.packed never comes

    quick["wait_seconds"] = 0
    assert run_command(monkeypatch, Silent(wms), quick) == 1
    err = capsys.readouterr().err
    first, last = demo.refs_for(AT)[0], demo.refs_for(AT)[-1]
    assert "seed-demo: Simple WMS packed 19 deliveries but 0 reached" in err
    assert ("Simple TMS now holds part of a demo day (and Simple WMS 19 deliveries made by it: "
            f"{first} to {last}, 19 of them packed): empty the Simple TMS database and pod_files "
            "before trying again.") in err


def test_any_failure_partway_says_it_is_half_made(db, client, wms, quick, monkeypatch, capsys):
    class Breaks(FakeWms):
        def pick_and_pack(self, ref, drop, n):
            if n == 2:
                raise RuntimeError("the cable came out")
            super().pick_and_pack(ref, drop, n)

    assert run_command(monkeypatch, Breaks(wms), quick) == 1
    err = capsys.readouterr().err
    assert "RuntimeError: the cable came out" in err
    assert "Simple TMS now holds part of a demo day (and Simple WMS 19 deliveries made by it: " in err
    assert "19, 2 of them packed): empty the Simple TMS database and pod_files" in err


def test_a_day_refused_at_the_start_says_nothing_of_half_made(db, client, wms, quick, monkeypatch,
                                                              capsys):
    db.add(Vehicle(rego="ABC123", type="van", max_kg=1, max_m3=1, active=True))
    db.commit()
    assert run_command(monkeypatch, FakeWms(wms), quick) == 1
    assert "part of a demo day" not in capsys.readouterr().err


class Answers:
    """Simple TMS's API as the seed sees it: a reply for each path, or a list
    of them in turn. Each request takes `takes` seconds on the clock."""

    def __init__(self, replies, clock=None, takes=0.0):
        self.replies, self.clock, self.takes, self.sent = replies, clock, takes, []

    def request(self, method, url, **kw):
        self.sent.append((method, url))
        if self.clock is not None:
            self.clock[0] += self.takes
        reply = self.replies[url]
        return reply.pop(0) if isinstance(reply, list) else reply


def seeder_over(replies):
    return demo.Seeder(Answers(replies), None, today=AT.date(), sleep=lambda s: None,
                       pace_minutes=0, wait_seconds=0, position_gap=0)


def test_an_answer_that_isnt_json_is_a_seed_error():
    seeder = seeder_over({"/v1/carriers": httpx.Response(200, text="<html>proxy</html>")})
    with pytest.raises(demo.SeedError, match="GET /carriers -> 200, not JSON: <html>proxy"):
        seeder.call("GET", "/carriers", {})

    def handler(request):
        return httpx.Response(202, text="<html>proxy</html>")

    with pytest.raises(demo.SeedError, match="Simple WMS: POST /deliveries -> 202, not JSON"):
        wms_over(handler).create("DEMO-261015-123000-01", demo.TRAY[1], AT.date(), 0)


def test_something_missing_from_an_answer_is_a_seed_error():
    seeder = seeder_over({
        "/v1/auth/login": httpx.Response(200, json={"token": "t"}),
        "/v1/carriers": httpx.Response(200, json={"items": [{"id": 1, "name": "Other"}]})})
    with pytest.raises(demo.SeedError, match="no carrier Valley Freight \\(sample\\) in GET /carriers"):
        seeder.book("Valley Freight (sample)", [1], "VF-", collect=False)


def test_a_delivery_that_wont_cancel_is_named():
    class Stuck:
        warehouse = "BAL-WH01"

        def cancel(self, ref):
            if ref.endswith("-02"):
                raise demo.SeedError("Simple WMS at http://wms.test didn't answer")

    made = ["DEMO-1-01", "DEMO-1-02", "DEMO-1-03"]
    said = demo.undo(Stuck(), made)
    assert made == ["DEMO-1-02"]
    assert said.startswith("Couldn't cancel 1 of the 3 deliveries it made in Simple WMS, so their "
                           "stock is still reserved: cancel them there: DEMO-1-02 (Simple WMS at")


def driving(replies, takes):
    """A seeder whose clock moves as it sleeps and as each request takes."""
    clock, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    api = Answers({"/v1/auth/driver-login": httpx.Response(200, json={"token": "t"}), **replies},
                  clock, takes)
    seeder = demo.Seeder(api, None, today=AT.date(), sleep=sleep, pace_minutes=0, wait_seconds=0,
                         position_gap=10, clock=lambda: clock[0])
    return seeder, api, slept


def test_positions_are_spaced_from_when_the_last_was_taken():
    seeder, api, slept = driving({"/v1/runs/7/position": httpx.Response(204)}, takes=4)
    seeder.drive({"id": 7}, "kim", [("-37.1", "143.1"), ("-37.2", "143.2")], 0)
    # the first took 4 s to land: the next waits the full 10 s from then
    assert slept == [10]


def test_a_position_too_soon_is_sent_again_once_after_a_wait():
    too_soon = httpx.Response(429, json={"detail": "one position every 10 seconds is plenty",
                                         "code": "too_often"})
    seeder, api, slept = driving({"/v1/runs/7/position": [too_soon, httpx.Response(204)]},
                                 takes=0)
    seeder.drive({"id": 7}, "kim", [("-37.1", "143.1")], 0)
    assert slept == [demo.TOO_OFTEN_WAIT]
    assert [url for _, url in api.sent].count("/v1/runs/7/position") == 2

    seeder, api, slept = driving({"/v1/runs/7/position": [too_soon, too_soon]}, takes=0)
    with pytest.raises(demo.SeedError, match="429.*too_often"):
        seeder.drive({"id": 7}, "kim", [("-37.1", "143.1")], 0)
