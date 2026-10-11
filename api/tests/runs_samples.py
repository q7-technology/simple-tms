"""Trucks, drivers and packed consignments made straight in the database,
so a planning test starts from a depot with freight waiting."""
from datetime import UTC, date, datetime
from decimal import Decimal

from tms.models import Consignment, ConsignmentEvent, Driver, Package, Vehicle
from tms.services.access import hash_password

DAY = date(2026, 10, 14)


def vehicle(db, rego="TMS041", max_kg="1200", max_m3="9", type="van", active=True) -> Vehicle:
    v = Vehicle(rego=rego, type=type, max_kg=Decimal(max_kg), max_m3=Decimal(max_m3),
                active=active)
    db.add(v)
    db.commit()
    return v


def driver(db, code="kim", name="Kim") -> Driver:
    d = Driver(code=code, name=name, pin_hash=hash_password("2468"), failed_attempts=0,
               locked=False, active=True)
    db.add(d)
    db.commit()
    return d


def consignment(db, ref="0080034519", customer="Station Deli", suburb="Ballan",
                kg="100", cm=("100", "100", "50"), owner="DEFAULT") -> Consignment:
    """One carton: 100 kg and 0.5 m³ unless told otherwise."""
    c = Consignment(wms_ref=ref, warehouse="BAL-WH01", owner=owner, customer=customer,
                    ship_to={"name": customer, "address": "1 Main St", "suburb": suburb,
                             "state": "VIC", "postcode": "3342"},
                    status="packed")
    c.packages.append(Package(package_no=1, type="carton", weight_kg=Decimal(kg),
                              length_cm=Decimal(cm[0]), width_cm=Decimal(cm[1]),
                              height_cm=Decimal(cm[2])))
    c.events.append(ConsignmentEvent(kind="packed", at=datetime.now(UTC), detail={}))
    db.add(c)
    db.commit()
    return c


def new_run(client, headers, rego="TMS041", code="kim", day=DAY) -> dict:
    r = client.post("/v1/runs", headers=headers,
                    json={"date": day.isoformat(), "vehicle": rego, "driver": code})
    assert r.status_code == 201, r.text
    return r.json()


def assign(client, headers, run_id: int, consignment_id: int, position: int | None = None):
    body = {"consignment_id": consignment_id}
    if position is not None:
        body["position"] = position
    return client.post(f"/v1/runs/{run_id}/assign", headers=headers, json=body)


def order(run: dict) -> list[int]:
    return [s["consignment"]["id"] for s in run["stops"]]
