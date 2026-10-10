"""Simple WMS events. Simple TMS is an ordinary subscriber: the body is
signed, an event id already seen is ignored, and anything we do not act on
still gets a 2xx so the WMS stops retrying it."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tms.api.deps import DB
from tms.api.errors import Unauthorised, field_errors, violated_constraint
from tms.config import get_settings
from tms.models import Consignment, InboundEvent
from tms.services import consignments, signature, wms_client

router = APIRouter(tags=["wms"])
log = logging.getLogger("tms.wms")

HANDLED = {"delivery.packed"}


class WmsEvent(BaseModel):
    """Simple WMS's outbound envelope. Unknown fields are ignored, so a newer
    WMS does not break us."""

    model_config = ConfigDict(extra="ignore")
    event_id: uuid.UUID
    event_type: str = Field(max_length=64)
    occurred_at: datetime
    warehouse: str | None = Field(default=None, max_length=32)
    owner: str = Field(default="DEFAULT", min_length=1, max_length=32)
    external_ref: str | None = Field(default=None, max_length=64)
    data: dict = Field(default_factory=dict)


# What a package's numeric(18, 6) columns hold: a 7th decimal place or a 13th
# digit before the point is refused, never rounded or left to the database.
Measure = Annotated[Decimal | None, Field(default=None, max_digits=18, decimal_places=6, ge=0)]


class PackedPackage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    package_no: int = Field(ge=1, lt=2 ** 31)
    type: str | None = Field(default=None, max_length=16)
    weight_kg: Measure = None
    length_cm: Measure = None
    width_cm: Measure = None
    height_cm: Measure = None
    sscc: str | None = Field(default=None, max_length=18)


class PackedData(BaseModel):
    """`data` of delivery.packed (simple-wms services/outbound.py finish_packing)."""

    model_config = ConfigDict(extra="ignore")
    delivery_ref: str | None = Field(default=None, min_length=1, max_length=64)
    packages: list[PackedPackage] = Field(default_factory=list)


class EventReply(BaseModel):
    status: str  # created, ignored, duplicate
    event_id: uuid.UUID
    consignment_id: int | None = None


async def raw_body(request: Request) -> bytes:
    """The bytes exactly as signed. Parsed only after the signature checks out."""
    return await request.body()


def _invalid(errors: list[dict]) -> JSONResponse:
    return JSONResponse(status_code=422, content={"errors": errors})


def _record(db, event: WmsEvent, outcome: str, consignment_id: int | None = None) -> EventReply:
    db.add(InboundEvent(event_id=event.event_id, event_type=event.event_type,
                        external_ref=event.external_ref, outcome=outcome))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if violated_constraint(exc) != "pk_inbound_event":
            raise
        # the same event arrived twice at once and the other one won
        return EventReply(status="duplicate", event_id=event.event_id)
    return EventReply(status=outcome, event_id=event.event_id, consignment_id=consignment_id)


def _consignment_for(db, owner: str, ref: str) -> Consignment | None:
    return db.execute(select(Consignment).where(
        Consignment.owner == owner, Consignment.wms_ref == ref)).scalar_one_or_none()


@router.post("/wms/events", response_model=EventReply)
def receive_wms_event(
    db: DB, body: Annotated[bytes, Depends(raw_body)],
    x_wms_signature: Annotated[str | None, Header()] = None,
):
    secret = get_settings().wms_webhook_secret
    if not secret:
        # 503, not 401: the WMS keeps the event and retries once it is set
        return JSONResponse(status_code=503, content={
            "detail": "TMS_WMS_WEBHOOK_SECRET is not set", "code": "not_configured"})
    if not signature.verify(secret, body, x_wms_signature):
        raise Unauthorised("missing or wrong X-WMS-Signature")
    try:
        # numbers as Decimal, so no weight ever passes through a float
        raw = json.loads(body, parse_float=Decimal)
    except ValueError:
        return _invalid([{"field": "body", "message": "not JSON"}])
    try:
        event = WmsEvent.model_validate(raw)
    except ValidationError as exc:
        return _invalid(field_errors(exc.errors()))

    if db.get(InboundEvent, event.event_id) is not None:
        log.info("event %s (%s) already seen; ignored", event.event_id, event.event_type)
        return EventReply(status="duplicate", event_id=event.event_id)

    if event.event_type not in HANDLED:
        log.info("event %s (%s) for %s: nothing for Simple TMS to do",
                 event.event_id, event.event_type, event.external_ref)
        return _record(db, event, "ignored")

    try:
        data = PackedData.model_validate(event.data)
    except ValidationError as exc:
        return _invalid(field_errors([{**e, "loc": ("data", *e["loc"])} for e in exc.errors()]))
    ref = data.delivery_ref or event.external_ref
    if not ref:
        return _invalid([{"field": "data.delivery_ref", "message": "no delivery reference"}])

    existing = _consignment_for(db, event.owner, ref)
    if existing is not None:
        log.warning("event %s: delivery %s already has consignment %s; ignored",
                    event.event_id, ref, existing.id)
        return _record(db, event, "ignored", existing.id)
    # no transaction held open while we wait on the WMS
    db.rollback()

    try:
        delivery = wms_client.get_delivery(ref, event.owner)
    except wms_client.WmsUnavailable as exc:
        # nothing recorded, so the WMS's retry (1 min, 5, 30, 2 h) tries again
        log.warning("event %s: could not read delivery %s from Simple WMS: %s",
                    event.event_id, ref, exc)
        return JSONResponse(status_code=503, content={
            "detail": f"could not read delivery {ref} from Simple WMS: {exc}",
            "code": "wms_unavailable"})

    try:
        delivery = wms_client.WmsDelivery.model_validate(delivery)
    except ValidationError as exc:
        # it will read no better on a retry, so 422, not 503; nothing is kept, and
        # Simple WMS shows the event as failed with this reason
        errors = [{"field": ".".join(["delivery", *(str(x) for x in e["loc"])]),
                   "message": f"delivery {ref} from Simple WMS: {e['msg']}"}
                  for e in exc.errors()]
        log.warning("event %s: delivery %s from Simple WMS does not read: %s",
                    event.event_id, ref, errors)
        return _invalid(errors)

    # the event id first, so a twin of this event fails on it, not on the consignment
    db.add(InboundEvent(event_id=event.event_id, event_type=event.event_type,
                        external_ref=event.external_ref, outcome="created"))
    try:
        db.flush()
        consignment = consignments.from_delivery_packed(
            db, event_id=str(event.event_id), occurred_at=event.occurred_at, owner=event.owner,
            warehouse=event.warehouse or delivery.warehouse or "", ref=ref,
            packages=[p.model_dump() for p in data.packages], delivery=delivery)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        constraint = violated_constraint(exc)
        if constraint == "pk_inbound_event":
            log.info("event %s (%s) arrived twice at once; this one ignored",
                     event.event_id, event.event_type)
            return EventReply(status="duplicate", event_id=event.event_id)
        if constraint == "uq_consignment_owner_wms_ref":
            # another event for the same delivery made its consignment meanwhile
            other = _consignment_for(db, event.owner, ref)
            log.warning("event %s: delivery %s got consignment %s meanwhile; ignored",
                        event.event_id, ref, other.id if other else None)
            return _record(db, event, "ignored", other.id if other else None)
        raise
    log.info("event %s: consignment %s for delivery %s", event.event_id, consignment.id, ref)
    return EventReply(status="created", event_id=event.event_id, consignment_id=consignment.id)
