"""What Simple WMS sends and answers, shaped as its code builds them
(simple-wms api/wms/services/events.py emit(), outbound.py finish_packing(),
api/routes/outbound.py DeliveryOut)."""
import json
import uuid

from tms.services.signature import sign


def packed_event(ref: str = "0080012345", event_id: str | None = None, owner: str = "DEFAULT") -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "delivery.packed",
        "occurred_at": "2026-10-10T03:12:00Z",
        "warehouse": "BAL-WH01",
        "owner": owner,
        "external_ref": ref,
        "data": {
            "delivery_ref": ref,
            "packages": [
                {"package_no": 1, "type": "carton", "weight_kg": "9.5", "length_cm": "60",
                 "width_cm": "40", "height_cm": "40", "sscc": "093123450000000012",
                 "container_id": None,
                 "contents": [{"delivery_line": 10, "sku": "GLV-L", "batch": None,
                               "qty": "20", "uom": "PR"}]},
                {"package_no": 2, "type": "satchel", "weight_kg": None, "length_cm": None,
                 "width_cm": None, "height_cm": None, "sscc": None, "container_id": None,
                 "contents": [{"delivery_line": 20, "sku": "TPE-48", "batch": None,
                               "qty": "6", "uom": "RL"}]},
            ],
        },
    }


def delivery(ref: str = "0080012345") -> dict:
    """The parts of GET /v1/deliveries/{ref} that Simple TMS reads."""
    return {
        "wms_id": "17", "external_ref": ref, "owner": "DEFAULT", "warehouse": "BAL-WH01",
        "status": "packed", "required_by": "2026-10-11",
        "ship_to": {"name": "Ballarat Hardware", "address": "12 Sturt St", "suburb": "Ballarat",
                    "state": "VIC", "postcode": "3350", "country": "AU", "phone": "03 5300 0000"},
        "lines": [], "packages": [],
    }


def body_of(event: dict) -> bytes:
    # compact, as the WMS worker sends it
    return json.dumps(event, separators=(",", ":")).encode()


def post_event(client, event: dict, secret: str, signature: str | None = "sign"):
    body = body_of(event)
    headers = {"Content-Type": "application/json", "X-WMS-Event-Id": event["event_id"],
               "X-WMS-Event-Type": event["event_type"]}
    if signature == "sign":
        headers["X-WMS-Signature"] = sign(secret, body)
    elif signature is not None:
        headers["X-WMS-Signature"] = signature
    return client.post("/v1/wms/events", content=body, headers=headers)
