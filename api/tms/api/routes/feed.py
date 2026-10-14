"""What just happened, for the map's feed."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel

from tms.api.deps import DB, Principal, require
from tms.services import feed

router = APIRouter(tags=["runs"])


class FeedItem(BaseModel):
    # packed, delivered, failed, left, finished, con_note, collected, prestart, fixed
    kind: str
    at: dt.datetime
    run_id: int | None
    run_no: str | None
    rego: str | None
    driver: str | None
    sequence: int | None
    customer: str | None
    wms_ref: str | None
    # failed: closed, refused, damaged, wrong_address
    reason: str | None
    # prestart: passed, failed or grounded, and the items that failed
    result: str | None
    failed: list[str]
    # who recorded it: a pre-start's sender, the fleet manager who marked it
    # fixed, who typed a con note or marked a manifest collected
    by: str | None
    note: str | None
    # con_note and collected: the carrier, the con note, the manifest and how many on it
    carrier: str | None
    con_note: str | None
    manifest_no: str | None
    count: int | None


class FeedOut(BaseModel):
    items: list[FeedItem]


@router.get("/feed", response_model=FeedOut)
def what_just_happened(db: DB, limit: int = Query(default=20, ge=1, le=100),
                       who: Principal = require("runs:read")):
    """Newest first: consignments packed in Simple WMS, delivered or not,
    runs leaving and finishing, con notes in and carriers collecting;
    pre-starts and trucks marked fixed only for a caller with fleet:read."""
    return FeedOut(items=[FeedItem(
        kind=h.kind, at=h.at, run_id=h.run.id if h.run else None,
        run_no=h.run.number if h.run else None, rego=h.rego, driver=h.driver,
        sequence=h.sequence, customer=h.customer, wms_ref=h.wms_ref, reason=h.reason,
        result=h.result, failed=h.failed, by=h.by, note=h.note, carrier=h.carrier,
        con_note=h.con_note, manifest_no=h.manifest_no, count=h.count)
        for h in feed.recent(db, who, limit)])
