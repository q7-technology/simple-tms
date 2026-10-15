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
