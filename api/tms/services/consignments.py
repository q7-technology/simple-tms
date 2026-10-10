"""Consignments from Simple WMS deliveries."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from tms.models import Consignment, ConsignmentEvent, Package
from tms.services.wms_client import WmsDelivery


def _dec(value) -> Decimal | None:
    # the WMS sends decimals as strings ("9.5"); never let a float in
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("a float would lose digits; parse JSON numbers as Decimal")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def from_delivery_packed(db: Session, *, event_id: str, occurred_at: datetime, owner: str,
                         warehouse: str, ref: str, packages: list[dict],
                         delivery: WmsDelivery) -> Consignment:
    """The packages come from the event, the ship-to from the delivery."""
    consignment = Consignment(
        wms_ref=ref, warehouse=warehouse, owner=owner,
        customer=delivery.customer(ref), ship_to=delivery.ship_to,
        required_by=delivery.required_by, status="packed",
    )
    for p in packages:
        consignment.packages.append(Package(
            package_no=int(p["package_no"]), type=p.get("type") or "carton",
            weight_kg=_dec(p.get("weight_kg")), length_cm=_dec(p.get("length_cm")),
            width_cm=_dec(p.get("width_cm")), height_cm=_dec(p.get("height_cm")),
            sscc=p.get("sscc"),
        ))
    consignment.events.append(ConsignmentEvent(
        kind="packed", at=occurred_at,
        detail={"wms_event_id": event_id, "packages": len(packages)},
    ))
    db.add(consignment)
    db.flush()
    return consignment
