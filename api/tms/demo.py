"""Demo data: a small Ballarat depot partway through its day.

Everything goes through the API, in process, as the people who would do it:
a fleet manager adds the trucks, drivers and carriers, the dispatcher plans
and books, and each driver signs in with their PIN to do the pre-start,
start, deliver and send where they are. So every rule applies, and the
history says who did what. Nothing here writes a table directly, but for the
two desk users, which only the command line makes (as `tms create-user` does).

The freight is real Simple WMS freight: each delivery is created, picked and
packed through Simple WMS's API with the key in TMS_WMS_API_KEY, and becomes
a consignment when Simple WMS sends its signed `delivery.packed`, as in
production. Times are when it runs: the API takes nothing back-dated, so the
run on the road drives for a few minutes while the seed waits.

Every business, name, number and picture is invented. The carriers say
"(sample)" in their names.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select

from tms import demo_pictures
from tms.db import get_sessionmaker
from tms.models import Carrier, Consignment, Driver, Run, User, Vehicle
from tms.services import access
from tms.services.fleet import add_months

PASSWORD = "demo-password-1"
PIN = "2468"
DEPOT_TZ = ZoneInfo("Australia/Melbourne")

# (username, display name, role): only the command line makes desk users
USERS = [("dee", "Dee", "dispatcher"), ("sam", "Sam", "manager")]

# (code, name)
DRIVERS = [("tom", "Tom"), ("kim", "Kim"), ("ana", "Ana"), ("raj", "Raj")]

# (rego, type, max kg, max m3, service every months, every km,
#  rego expires in days, last service months ago (+ days), its odometer)
VEHICLES = [
    ("TMS-033", "van", "1200", "8.5", 6, 15000, 212, (2, 0), 48210),
    ("TMS-041", "rigid", "4500", "22", 6, 15000, 140, (3, 0), 61208),
    # serviced six months ago less 18 days: amber, service due
    ("TMS-052", "van", "1200", "8.5", 6, 15000, 301, (6, -18), 39875),
    ("TMS-027", "ute", "800", "2.5", 12, 20000, 96, (4, 0), 30120),
]

# the rigid also has its tail lift checked
RIGID_CHECKLIST = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
                   ("Load restraints", False), ("Tail lift", False), ("Damage", False)]

# (name, account, pickup time)
CARRIERS = [("Ridgeline Express (sample)", "RX-4410", "15:30"),
            ("Valley Freight (sample)", "VF-2071", "11:00")]


@dataclass(frozen=True)
class Drop:
    """One delivery: who it is for, where, and its cartons, each (type, kg,
    (l, w, h) cm). The phone is from the range kept for fiction, 03 5550 xxxx;
    the latitude and longitude are where the driver stands to deliver it."""
    customer: str
    address: str
    suburb: str
    postcode: str
    contact: str
    cartons: tuple[tuple[str, str, tuple[str, str, str]], ...]
    latitude: str
    longitude: str


SMALL = ("carton", "6.5", ("40", "30", "25"))
MEDIUM = ("carton", "14", ("60", "40", "40"))
LARGE = ("carton", "22", ("80", "60", "50"))
SATCHEL = ("satchel", "1.2", ("40", "25", "5"))


def pallet(kg: str) -> tuple[str, str, tuple[str, str, str]]:
    return ("pallet", kg, ("120", "100", "120"))


# the morning run (R-0001): three delivered, one came back
MORNING = [
    Drop("Bluestone Bakehouse", "210 Main Rd", "Ballarat East", "3350", "Nina", (MEDIUM, SMALL),
         "-37.5652", "143.8742"),
    Drop("Wattle Street Books", "18 Wattle St", "Clunes", "3370", "Owen", (SMALL,),
         "-37.2942", "143.7866"),
    Drop("Lakeside Rowing Club", "3 Boatshed Ln", "Wendouree", "3355", "Mei", (LARGE,),
         "-37.5468", "143.8371"),
    Drop("Ironbark Cafe", "41 Learmonth St", "Buninyong", "3357", "Jas", (SMALL, SATCHEL),
         "-37.6505", "143.8846"),
]
# the run on the road (R-0002): two delivered, three to go
ROAD = [
    Drop("Gold Hill Pharmacy", "77 Albert St", "Sebastopol", "3356", "Priya", (SMALL, SATCHEL),
         "-37.5853", "143.8398"),
    Drop("Redgum Vet Clinic", "9 Stawell St", "Redan", "3350", "Callum", (MEDIUM,),
         "-37.5741", "143.8362"),
    Drop("Mill Lane Florist", "14 Mill Ln", "Mount Clear", "3350", "Rosa", (MEDIUM, MEDIUM),
         "-37.6003", "143.8701"),
    Drop("Creekside Garden Supplies", "260 Midland Hwy", "Creswick", "3363", "Ben",
         (pallet("310"),), "-37.4258", "143.8941"),
    Drop("Two Bridges Hardware", "5 Inglis St", "Ballan", "3342", "Lou", (LARGE, MEDIUM),
         "-37.5991", "144.2265"),
]
# planned for this afternoon (R-0003): a van nearly full
LATER = [
    Drop("Hilltop Joinery", "4 Quarry Rd", "Bacchus Marsh", "3340", "Dan", (pallet("480"),),
         "-37.6750", "144.4381"),
    Drop("Pinecrest Builders", "12 Depot Rd", "Smythesdale", "3351", "Kara", (pallet("420"),),
         "-37.6421", "143.6893"),
    Drop("Woodend Cheese Room", "88 High St", "Woodend", "3442", "Theo", (pallet("180"),),
         "-37.3557", "144.5263"),
]
# collected by Valley Freight this morning
VALLEY = [
    Drop("Maryborough Office Supplies", "30 Nolan St", "Maryborough", "3465", "Gail", (MEDIUM,),
         "-37.0459", "143.7396"),
    Drop("Beaufort Tyre and Auto", "2 Neill St", "Beaufort", "3373", "Sid", (LARGE, LARGE),
         "-37.4302", "143.3832"),
]
# on the carrier bay for Ridgeline this afternoon
RIDGELINE = [
    Drop("Kyneton Kitchenware", "60 Mollison St", "Kyneton", "3444", "Ivy", (MEDIUM, SMALL),
         "-37.2470", "144.4530"),
    Drop("Ararat Saddlery", "101 Barkly St", "Ararat", "3377", "Huw", (LARGE,),
         "-37.2833", "142.9273"),
    Drop("Castlemaine Print Shop", "7 Lyttleton St", "Castlemaine", "3450", "Ada", (SATCHEL,),
         "-37.0640", "144.2173"),
]
# in the tray, for tomorrow
TRAY = [
    Drop("Learmonth Feed and Grain", "15 High St", "Learmonth", "3352", "Ross", (pallet("260"),),
         "-37.4220", "143.7130"),
    Drop("Alfredton Dental Studio", "9 Cuthberts Rd", "Alfredton", "3350", "Leah", (SMALL,),
         "-37.5560", "143.8020"),
]
ALL = MORNING + ROAD + LATER + VALLEY + RIDGELINE + TRAY

# where the run on the road is, leg by leg: from the depot (out on the Western
# Fwy side of town) to stop 1, stop 1 to 2, then on towards 3
ROUTE = [
    [("-37.5402", "143.8105"), ("-37.5531", "143.8230"), ("-37.5690", "143.8331"),
     ("-37.5853", "143.8398")],
    [("-37.5812", "143.8380"), ("-37.5770", "143.8366"), ("-37.5741", "143.8362")],
    [("-37.5795", "143.8455"), ("-37.5868", "143.8560"), ("-37.5931", "143.8634")],
]


class SeedError(Exception):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


class Wms:
    """Simple WMS through its API: a delivery created, picked and packed, as
    the scanner would. Simple WMS then sends `delivery.packed` to its
    subscribers, Simple TMS among them."""

    def __init__(self, url: str, key: str, *, warehouse: str, operator: str,
                 skus: list[tuple[str, str]], client: httpx.Client | None = None):
        if not url or not key:
            raise SeedError("TMS_WMS_URL and TMS_WMS_API_KEY are needed: the freight comes from "
                            "Simple WMS")
        self.url, self.warehouse, self.operator, self.skus = url.rstrip("/"), warehouse, operator, skus
        self.http = client or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"Bearer {key}"}

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.http.request(method, f"{self.url}/v1{path}", headers=self.headers, **kw)
        except httpx.HTTPError as e:
            raise SeedError(f"Simple WMS at {self.url} didn't answer: {e}") from None
        if r.status_code >= 300:
            raise SeedError(f"Simple WMS: {method} {path} -> {r.status_code}: {r.text}")
        return r.json() if r.content else {}

    def _post(self, path: str, body: dict) -> dict:
        return self._call("POST", path, json={"message_id": str(uuid.uuid4()), **body})

    def pack(self, ref: str, drop: Drop, required_by: date, n: int) -> None:
        """One delivery with a line per carton, picked in full and packed a
        carton per line."""
        lines = []
        for i, _ in enumerate(drop.cartons, start=1):
            sku, uom = self.skus[(n + i) % len(self.skus)]
            lines.append({"delivery_line": 10 * i, "sku": sku, "batch": None,
                          "qty": 1 + (n + i) % 3, "uom": uom})
        reply = self._post("/deliveries", {
            "external_ref": ref, "warehouse": self.warehouse, "owner": "DEFAULT",
            "pick_mode": "single", "priority": "normal", "required_by": required_by.isoformat(),
            "ship_to": {"name": drop.customer, "address": drop.address, "suburb": drop.suburb,
                        "state": "VIC", "postcode": drop.postcode, "country": "AU",
                        "contact": drop.contact, "phone": f"03 5550 {1000 + n:04d}"},
            "carrier_hint": None, "allow_short": False, "lines": lines})
        short = [a for a in reply.get("allocation", []) if float(a.get("short") or 0) > 0]
        if short:
            names = ", ".join(f"{a['sku']} ({a['short']} short)" for a in short)
            raise SeedError(f"Simple WMS hasn't the stock for {ref} at {self.warehouse}: {names}. "
                            "Name SKUs with stock there in --skus.")
        tasks = self._call("GET", "/tasks", params={"warehouse": self.warehouse,
                                                    "source_ref": ref, "type": "pick"})["items"]
        if not tasks:
            raise SeedError(f"Simple WMS raised no pick task for {ref}")
        task = tasks[-1]
        self._post(f"/tasks/{task['wms_id']}/start", {"operator": self.operator})
        # what was picked for each delivery line, by batch: a carton carries the batch picked
        picked: dict[int, list[dict]] = {}
        for line in task["lines"]:
            self._post(f"/tasks/{task['wms_id']}/lines/{line['line_no']}/confirm",
                       {"qty": line["expected_qty"], "uom": line["uom"], "batch": line["batch"],
                        "operator": self.operator})
            picked.setdefault(line["source_line"], []).append(
                {"delivery_line": line["source_line"], "sku": line["sku"], "batch": line["batch"],
                 "qty": line["expected_qty"], "uom": line["uom"]})
        self._post(f"/deliveries/{ref}/pack", {
            "warehouse": self.warehouse, "packed_by": self.operator, "complete": True,
            "packages": [{"package_no": i, "type": kind, "weight_kg": kg, "length_cm": dims[0],
                          "width_cm": dims[1], "height_cm": dims[2],
                          "lines": picked[line["delivery_line"]]}
                         for i, ((kind, kg, dims), line) in enumerate(zip(drop.cartons, lines), 1)]})


@dataclass
class Made:
    """What the seed made, for the summary."""
    day: date
    refs: list[str]
    runs: dict[str, dict]
    manifests: dict[str, dict]
    printed: bool
    tray: int
    came_back: int
    vehicles: dict[str, str]


class Seeder:
    def __init__(self, client, wms, *, today: date, sleep: Callable[[float], None],
                 pace_minutes: float, wait_seconds: float, position_gap: float):
        self.c, self.wms, self.today = client, wms, today
        self.sleep, self.pace, self.wait, self.gap = sleep, pace_minutes, wait_seconds, position_gap
        self.drivers: dict[str, dict] = {}
        self.last_position: dict[int, float] = {}
        self.printed = True

    # --- the API, as a person ----------------------------------------------

    def call(self, method: str, path: str, headers: dict, ok=(200, 201, 202, 204), **kw):
        r = self.c.request(method, f"/v1{path}", headers=headers, **kw)
        if r.status_code not in ok:
            raise SeedError(f"{method} {path} -> {r.status_code}: {r.text}")
        return r.json() if r.content else {}

    def desk(self, username: str) -> dict:
        """A fresh sign-in each time: a desk token lasts 15 minutes."""
        body = self.call("POST", "/auth/login", {}, json={"username": username, "password": PASSWORD})
        return {"Authorization": f"Bearer {body['token']}"}

    def driver(self, code: str) -> dict:
        if code not in self.drivers:
            body = self.call("POST", "/auth/driver-login", {}, json={"code": code, "pin": PIN})
            self.drivers[code] = {"Authorization": f"Bearer {body['token']}"}
        return self.drivers[code]

    # --- the fleet, by the fleet manager -------------------------------------

    def fleet(self) -> None:
        h = self.desk("sam")
        self.call("PUT", "/checklists/rigid", h, json={
            "items": [{"label": label, "critical": critical} for label, critical in RIGID_CHECKLIST]})
        for rego, kind, kg, m3, months, km, rego_days, (ago, plus), odo in VEHICLES:
            self.call("POST", "/vehicles", h, json={
                "rego": rego, "type": kind, "max_kg": kg, "max_m3": m3,
                "service_every_months": months, "service_every_km": km})
            self.call("POST", f"/vehicles/{rego}/rego", h, json={
                "expires_on": (self.today + timedelta(days=rego_days)).isoformat(),
                "note": "Renewed online"})
            done = add_months(self.today, -ago) + timedelta(days=-plus)
            self.call("POST", f"/vehicles/{rego}/service", h, json={
                "done_on": done.isoformat(), "odometer_km": odo,
                "note": f"{odo // 1000 * 1000:,} km service"})
        for code, name in DRIVERS:
            self.call("POST", "/drivers", h, json={"code": code, "name": name, "pin": PIN})
        for name, account, pickup in CARRIERS:
            self.call("POST", "/carriers", h, json={"name": name, "account": account,
                                                    "pickup_time": pickup})

    # --- the freight, from Simple WMS ----------------------------------------

    def freight(self, refs: list[str]) -> dict[str, int]:
        """Pack each delivery in Simple WMS, then wait for its consignment."""
        tomorrow = self.today + timedelta(days=1)
        for n, (ref, drop) in enumerate(zip(refs, ALL)):
            self.wms.pack(ref, drop, tomorrow if drop in TRAY else self.today, n)
        h = self.desk("dee")
        prefix = refs[0][:-2]
        deadline = time.monotonic() + self.wait
        while True:
            items = self.call("GET", "/consignments", h, params={"q": prefix, "limit": 100})["items"]
            found = {c["wms_ref"]: c["id"] for c in items if c["wms_ref"] in refs}
            if len(found) == len(refs):
                return found
            if time.monotonic() > deadline:
                raise SeedError(
                    f"Simple WMS packed {len(refs)} deliveries but {len(found)} reached Simple TMS "
                    f"within {self.wait:.0f} s. Check Simple WMS's simple-tms subscriber "
                    "(GET /v1/subscribers) and its worker's log.")
            self.sleep(2)

    # --- runs, by the dispatcher -----------------------------------------------

    def plan(self, rego: str, driver: str, ids: list[int]) -> dict:
        h = self.desk("dee")
        run = self.call("POST", "/runs", h, json={"date": self.today.isoformat(),
                                                  "vehicle": rego, "driver": driver})
        for cid in ids:
            run = self.call("POST", f"/runs/{run['id']}/assign", h, json={"consignment_id": cid})
        if ids:
            self.print(f"/runs/{run['id']}/print", h)
        return run

    def print(self, path: str, h: dict) -> None:
        r = self.c.post(f"/v1{path}", headers=h)
        if r.status_code == 409 and r.json().get("code") == "printing_not_set_up":
            self.printed = False
        elif r.status_code != 202:
            raise SeedError(f"POST {path} -> {r.status_code}: {r.text}")

    def prestart(self, run: dict, code: str, odometer_km: int, *, fail: str | None = None) -> dict:
        h = self.driver(code)
        mine = self.call("GET", "/driver/runs", h, params={"date": self.today.isoformat()})
        listed = next(r for r in mine if r["id"] == run["id"])
        photo = None
        if fail:
            photo = self.upload(h, "photo", demo_pictures.tail_light_photo(run["id"]))
        items = []
        for line in listed["checklist"]:
            if line["label"] == fail:
                items.append({"item_id": line["id"], "result": "fail", "photo_file": photo,
                              "note": "Left tail light out and the lens is cracked."})
            else:
                items.append({"item_id": line["id"], "result": "pass"})
        return self.call("POST", f"/runs/{run['id']}/prestart", h, json={
            "message_id": str(uuid.uuid4()), "at": iso(now()), "vehicle": run["vehicle"]["rego"],
            "odometer_km": odometer_km, "items": items})

    def start(self, run: dict, code: str) -> None:
        self.call("POST", f"/runs/{run['id']}/start", self.driver(code),
                  json={"message_id": str(uuid.uuid4())})

    def upload(self, h: dict, kind: str, picture: bytes) -> str:
        file_id = str(uuid.uuid4())
        self.call("PUT", f"/pod-files/{file_id}", {**h, "Content-Type": "image/png"},
                  params={"kind": kind}, content=picture)
        return file_id

    def deliver(self, run: dict, code: str, cid: int, drop: Drop, *, signed: str | None,
                photo: bool, seed: int) -> None:
        h = self.driver(code)
        stops = self.call("GET", f"/runs/{run['id']}/stops", h)["stops"]
        stop = next(s for s in stops if s["consignment_id"] == cid)
        body = {"message_id": str(uuid.uuid4()), "at": iso(now()),
                "packages": [{"package_no": p["package_no"], "how": "scan" if i % 2 == 0 else "tick"}
                             for i, p in enumerate(stop["packages"])],
                "signed_name": signed, "note": None,
                "position": {"latitude": drop.latitude, "longitude": drop.longitude,
                             "accuracy_m": "9"}}
        if signed:
            body["signature_file"] = self.upload(h, "signature", demo_pictures.signature(seed))
        if photo:
            body["photo_file"] = self.upload(h, "photo", demo_pictures.parcel_photo(seed))
        self.call("POST", f"/runs/{run['id']}/stops/{cid}/deliver", h, json=body)

    def fail(self, run: dict, code: str, cid: int, drop: Drop, seed: int) -> None:
        h = self.driver(code)
        self.call("POST", f"/runs/{run['id']}/stops/{cid}/fail", h, json={
            "message_id": str(uuid.uuid4()), "at": iso(now()), "reason": "closed",
            "note": "Closed sign up. Rang the number, no answer.",
            "photo_file": self.upload(h, "photo", demo_pictures.parcel_photo(seed)),
            "position": {"latitude": drop.latitude, "longitude": drop.longitude, "accuracy_m": "11"}})

    def finish(self, run: dict, code: str) -> None:
        self.call("POST", f"/runs/{run['id']}/finish", self.driver(code),
                  json={"message_id": str(uuid.uuid4())})

    def drive(self, run: dict, code: str, leg: list[tuple[str, str]], minutes: float) -> None:
        """The phone's positions along one leg, spread over `minutes`, never
        closer than the API takes them (one a run every 10 seconds)."""
        gap = max(self.gap, minutes * 60 / len(leg))
        for lat, lng in leg:
            if run["id"] in self.last_position:
                self.sleep(max(0.0, gap - (time.monotonic() - self.last_position[run["id"]])))
            self.last_position[run["id"]] = time.monotonic()
            self.call("POST", f"/runs/{run['id']}/position", self.driver(code), json={
                "at": iso(now()), "latitude": lat, "longitude": lng, "accuracy_m": "8"})

    # --- carriers, by the dispatcher -------------------------------------------

    def book(self, carrier: str, ids: list[int], prefix: str, *, collect: bool) -> dict:
        h = self.desk("dee")
        carriers = self.call("GET", "/carriers", h)["items"]
        carrier_id = next(c["id"] for c in carriers if c["name"] == carrier)
        manifest = None
        for cid in ids:
            manifest = self.call("POST", f"/carriers/{carrier_id}/book", h, json={
                "consignment_id": cid, "date": self.today.isoformat()})
        for n, cid in enumerate(ids, start=1):
            manifest = self.call("POST", f"/manifests/{manifest['id']}/con-note", h, json={
                "consignment_id": cid, "con_note": f"{prefix}{n:03d}"})
        self.print(f"/manifests/{manifest['id']}/print", h)
        if collect:
            manifest = self.call("POST", f"/manifests/{manifest['id']}/collect", h, json={
                "message_id": str(uuid.uuid4()), "consignment_ids": ids})
        return manifest

    # --- the day ------------------------------------------------------------

    def run(self, refs: list[str]) -> Made:
        self.fleet()
        ids = self.freight(refs)
        by_drop = {drop: ids[ref] for ref, drop in zip(refs, ALL)}
        stamp = refs[0][3:-2]

        # the day's runs, planned first thing: R-0001 to R-0004
        r1 = self.plan("TMS-033", "tom", [by_drop[d] for d in MORNING])
        r2 = self.plan("TMS-041", "kim", [by_drop[d] for d in ROAD])
        r3 = self.plan("TMS-052", "ana", [by_drop[d] for d in LATER])
        r4 = self.plan("TMS-027", "raj", [])
        runs = {"morning": r1, "road": r2, "later": r3, "grounded": r4}
        manifests = {}

        # the morning run: out, three delivered, one closed, back
        self.prestart(r1, "tom", 48655)
        self.start(r1, "tom")
        for n, drop in enumerate(MORNING[:3]):
            self.deliver(r1, "tom", by_drop[drop], drop, signed=["Nina", "Owen", None][n],
                         photo=n != 1, seed=10 + n)
        self.fail(r1, "tom", by_drop[MORNING[3]], MORNING[3], seed=13)
        self.finish(r1, "tom")

        # Valley Freight called at 11
        manifests["valley"] = self.book("Valley Freight (sample)", [by_drop[d] for d in VALLEY],
                                        f"VF-{stamp}", collect=True)

        # the ute failed its pre-start: grounded, its run waiting for another truck
        self.prestart(r4, "raj", 30988, fail="Lights")

        # Ridgeline's pickup at 3:30: on the bay, con notes in, manifest printed
        manifests["ridgeline"] = self.book("Ridgeline Express (sample)",
                                           [by_drop[d] for d in RIDGELINE], f"RX-{stamp}",
                                           collect=False)

        # the run on the road: out, two delivered, on its way to the third
        self.prestart(r2, "kim", 63540)
        self.start(r2, "kim")
        for n, drop in enumerate(ROAD[:2]):
            self.drive(r2, "kim", ROUTE[n], self.pace)
            self.deliver(r2, "kim", by_drop[drop], drop, signed=["Priya", "Callum"][n],
                         photo=n == 0, seed=20 + n)
        self.drive(r2, "kim", ROUTE[2], self.pace / 2)

        h = self.desk("dee")
        runs = {k: self.call("GET", f"/runs/{r['id']}", h) for k, r in runs.items()}
        manifests = {k: self.call("GET", f"/manifests/{m['id']}", h) for k, m in manifests.items()}
        tray = self.call("GET", "/consignments", h, params={"status": "packed,failed"})["total"]
        came_back = self.call("GET", "/consignments", h, params={"status": "failed"})["total"]
        vehicles = {v["rego"]: v["state"] for v in self.call(
            "GET", "/vehicles", h, params={"date": self.today.isoformat()})["items"]}
        return Made(day=self.today, refs=refs, runs=runs, manifests=manifests,
                    printed=self.printed, tray=tray, came_back=came_back,
                    vehicles=vehicles)


def refs_for(at: datetime) -> list[str]:
    """Simple WMS delivery numbers, new each minute, so a second demo day
    against the same Simple WMS doesn't reuse the first's."""
    stamp = (int(at.timestamp()) // 60) % 100000
    return [f"008{stamp:05d}{n:02d}" for n in range(1, len(ALL) + 1)]


def check_empty() -> None:
    with get_sessionmaker()() as db:
        there = [name for name, model in (("vehicles", Vehicle), ("drivers", Driver),
                                         ("carriers", Carrier), ("runs", Run),
                                         ("consignments", Consignment))
                 if db.execute(select(func.count()).select_from(model)).scalar_one()]
        taken = sorted(db.execute(select(User.username).where(
            User.username.in_([u for u, _, _ in USERS]))).scalars())
    if there or taken:
        what = ", ".join(there + [f"user {u}" for u in taken])
        raise SeedError(f"already here: {what}. Demo data goes into an empty database.")


def make_users() -> None:
    with get_sessionmaker()() as db:
        for username, name, role in USERS:
            db.add(User(username=username, display_name=name, role=role,
                        password_hash=access.hash_password(PASSWORD), failed_attempts=0,
                        active=True))
        db.commit()


def seed_demo(wms: Wms, *, at: datetime | None = None, sleep: Callable[[float], None] = time.sleep,
              pace_minutes: float = 3, wait_seconds: float = 120, position_gap: float = 11) -> Made:
    """Fill an empty database with a demo day. Refuses one with any fleet,
    carriers, runs or consignments in it, or the demo's desk users."""
    import warnings

    with warnings.catch_warnings():
        # Starlette's test client prefers httpx2; the image has httpx, which works
        warnings.simplefilter("ignore", DeprecationWarning)
        from fastapi.testclient import TestClient

    from tms.api.app import app

    check_empty()
    at = at or now()
    make_users()
    with TestClient(app) as client:
        seeder = Seeder(client, wms, today=at.astimezone(DEPOT_TZ).date(), sleep=sleep,
                        pace_minutes=pace_minutes, wait_seconds=wait_seconds,
                        position_gap=position_gap)
        return seeder.run(refs_for(at))


def summary(made: Made) -> list[str]:
    """What was made, a line each. Never a key."""
    def run_line(r: dict, what: str) -> str:
        return f"{r['run_no']} {r['vehicle']['rego']} {r['driver']['name']}: {what}"

    def outcomes(r: dict, outcome: str) -> int:
        return sum(1 for s in r["stops"] if s["outcome"] == outcome)

    runs, manifests = made.runs, made.manifests
    road, later = runs["road"], runs["later"]
    blocked = runs["grounded"]["vehicle"]["blocked"]
    lines = [
        f"demo day for {made.day:%a %d %b %Y}, Ballarat",
        f"fleet: {len(VEHICLES)} trucks ("
        + ", ".join(f"{rego} {state}" for rego, state in sorted(made.vehicles.items()))
        + f"), {len(DRIVERS)} drivers, {len(CARRIERS)} carriers",
        f"simple wms: {len(made.refs)} deliveries packed ({made.refs[0]} to {made.refs[-1]}), "
        f"each a consignment",
        run_line(runs["morning"], f"finished, {outcomes(runs['morning'], 'delivered')} delivered, "
                                  f"{outcomes(runs['morning'], 'failed')} came back"),
        run_line(road, f"on the road, {outcomes(road, 'delivered')} of {len(road['stops'])} delivered"),
        run_line(later, f"planned, {len(later['stops'])} stops, load {later['load']['state']}"),
        run_line(runs["grounded"], f"planned, no stops: {blocked['message'] if blocked else 'not blocked'}"),
    ]
    for m in manifests.values():
        state = "collected" if m["status"] == "collected" else "on the carrier bay"
        lines.append(f"{m['manifest_no']} {m['carrier']['name']}: {state}, "
                     f"{m['totals']['consignments']} with con notes")
    lines.append(f"tray: {made.tray} to plan ({made.came_back} came back)")
    lines.append("run sheets and manifests sent to Platen" if made.printed else
                 "nothing printed: printing isn't set up (TMS_PLATEN_URL)")
    lines.append(f"desktop: sign in as dee (dispatcher) or sam (manager) with password {PASSWORD}")
    lines.append(f"driver app: tom, kim, ana or raj with PIN {PIN}")
    return lines
