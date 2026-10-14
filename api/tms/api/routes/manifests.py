"""The carrier bay: consignments booked with outside carriers, on each
carrier's manifest for the day, with their con notes."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, union, update

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError
from tms.api.routes.carriers import CarrierOut, carrier_out
from tms.api.routes.runs import (
    RunSheetOut, ShippingOut, StopConsignment, shipping_out, stop_consignment,
)
from tms.api.schemas import Dec, Page
from tms.models import Booking, Consignment, Manifest, OutboundCall, PrintJob
from tms.services import carriers
from tms.services.envelope import handle
from tms.services.load import consignment_totals, total

router = APIRouter(tags=["carriers"])


class LineOut(BaseModel):
    consignment: StopConsignment
    con_note: str | None
    added_at: dt.datetime


class TotalsOut(BaseModel):
    consignments: int
    packages: int
    cartons: int
    weight_kg: Dec


class AlreadyShippedOut(BaseModel):
    """A consignment Simple WMS had shipped already when the carrier took it
    (it came back from its run): Simple WMS kept what it had, this carrier
    and tracking number, as far as Simple TMS told it (null if not known)."""
    consignment_id: int
    wms_ref: str
    customer: str
    carrier: str | None
    tracking_no: str | None


class ManifestOut(BaseModel):
    id: int
    manifest_no: str
    date: dt.date
    # open (on the carrier bay), collected (the carrier has it)
    status: str
    carrier: CarrierOut
    created_by: str
    created_at: dt.datetime
    collected_at: dt.datetime | None
    collected_by: str | None
    lines: list[LineOut]
    totals: TotalsOut
    # how many on it have no con note yet
    needs_con_note: int
    # the latest print of it, or null
    print_job: RunSheetOut | None
    # the ship calls to Simple WMS once it is collected
    shipping: ShippingOut
    # what Simple WMS answered already_shipped for, keeping its run's
    already_shipped: list[AlreadyShippedOut]


def already_shipped(db, manifest_id: int) -> list[AlreadyShippedOut]:
    """Each ship call of the manifest that Simple WMS answered already_shipped,
    with what Simple TMS first told it shipped that consignment (its run)."""
    out = []
    for call, c in db.execute(
            select(OutboundCall, Consignment).join(Consignment)
            .where(OutboundCall.manifest_id == manifest_id, OutboundCall.already_shipped)
            .order_by(OutboundCall.id)).all():
        first = db.execute(select(OutboundCall).where(
            OutboundCall.consignment_id == c.id, OutboundCall.id != call.id,
            OutboundCall.status == "done", OutboundCall.already_shipped.is_(False))
            .order_by(OutboundCall.done_at, OutboundCall.id).limit(1)).scalar_one_or_none()
        out.append(AlreadyShippedOut(
            consignment_id=c.id, wms_ref=c.wms_ref, customer=c.customer,
            carrier=first.body.get("carrier") if first else None,
            tracking_no=first.body.get("tracking_no") if first else None))
    return out


def manifest_out(db, m: Manifest) -> ManifestOut:
    t = total(consignment_totals(b.consignment.packages) for b in m.lines)
    job = db.execute(select(PrintJob).where(PrintJob.manifest_id == m.id)
                     .order_by(PrintJob.id.desc()).limit(1)).scalar_one_or_none()
    return ManifestOut(
        id=m.id, manifest_no=m.number, date=m.day,
        status="open" if m.collected_at is None else "collected",
        carrier=carrier_out(m.carrier), created_by=m.created_by, created_at=m.created_at,
        collected_at=m.collected_at, collected_by=m.collected_by,
        lines=[LineOut(consignment=stop_consignment(b.consignment), con_note=b.con_note,
                       added_at=b.added_at) for b in m.lines],
        totals=TotalsOut(consignments=len(m.lines), packages=t.packages, cartons=t.cartons,
                         weight_kg=t.weight_kg),
        needs_con_note=sum(b.con_note is None for b in m.lines),
        print_job=None if job is None else RunSheetOut(
            job_id=job.job_id, status=job.status, printer=job.printer,
            last_error=job.last_error, created_at=job.created_at, printed_at=job.printed_at),
        shipping=shipping_out(db, OutboundCall.manifest_id == m.id), already_shipped=already_shipped(db, m.id))


def _fresh(db, manifest_id: int) -> ManifestOut:
    db.commit()
    return manifest_out(db, carriers.get_manifest(db, manifest_id))


class BookIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    consignment_id: int = Field(ge=1)
    # the day the carrier is to collect it, by the dispatcher's calendar
    day: dt.date = Field(alias="date")
    con_note: str | None = Field(default=None, max_length=64)


class ConsignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)


class ConNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)
    con_note: str = Field(max_length=64)


@router.post("/carriers/{id}/book", response_model=ManifestOut)
def book(id: int, body: BookIn, db: DB, who: Principal = require("runs:write")):
    """Onto the carrier's manifest for the day; answers with that manifest."""
    m = carriers.book(db, id, body.consignment_id, body.day, body.con_note, by=who.name)
    return _fresh(db, m.id)


@router.get("/manifests", response_model=Page[ManifestOut])
def list_manifests(db: DB, day: dt.date = Query(alias="date"), left_open: bool = False,
                   collected_since: dt.datetime | None = None,
                   who: Principal = require("runs:read")):
    """One day's manifests with something on them, open or collected, oldest
    first. With left_open, first the ones from before that day still waiting
    for their carrier. With collected_since (the start of the dispatcher's
    day), also those from earlier days collected since then."""
    live = select(Booking.manifest_id).where(Booking.removed_at.is_(None))
    parts = [select(Manifest.id).where(Manifest.day == day, Manifest.id.in_(live))]
    if left_open:
        parts.append(select(Manifest.id).where(
            Manifest.day < day, Manifest.collected_at.is_(None), Manifest.id.in_(live)))
    if collected_since is not None:
        if collected_since.tzinfo is None:
            raise FieldError("collected_since", "give the time with its offset, e.g. "
                                                "2026-10-15T00:00:00+11:00")
        parts.append(select(Manifest.id).where(
            Manifest.day < day, Manifest.collected_at >= collected_since,
            Manifest.id.in_(live)))
    query = union(*parts)
    ids = db.execute(select(Manifest.id).where(Manifest.id.in_(query))
                     .order_by(Manifest.day, Manifest.id)).scalars().all()
    return Page(items=[manifest_out(db, carriers.get_manifest(db, i)) for i in ids],
                total=len(ids))


@router.get("/manifests/{id}", response_model=ManifestOut)
def read_manifest(id: int, db: DB, who: Principal = require("runs:read")):
    return manifest_out(db, carriers.get_manifest(db, id))


@router.post("/manifests/{id}/con-note", response_model=ManifestOut)
def con_note(id: int, body: ConNoteIn, db: DB, who: Principal = require("runs:write")):
    carriers.set_con_note(db, id, body.consignment_id, body.con_note, by=who.name)
    return _fresh(db, id)


@router.post("/manifests/{id}/unbook", response_model=ManifestOut)
def unbook(id: int, body: ConsignmentIn, db: DB, who: Principal = require("runs:write")):
    carriers.unbook(db, id, body.consignment_id, by=who.name)
    return _fresh(db, id)


class CollectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: uuid.UUID
    # what is on the manifest as the dispatcher saw it: anything else is stale
    consignment_ids: list[int] = Field(max_length=500)


@router.post("/manifests/{id}/collect", response_model=ManifestOut)
def collect(id: int, body: CollectIn, db: DB, who: Principal = require("runs:write")):
    """The carrier has taken it: each consignment is marked shipped in Simple
    WMS with the carrier's name and its con note. Once per message_id."""
    def work():
        m = carriers.collect(db, id, body.consignment_ids, by=who.name)
        return manifest_out(db, m)
    return handle(db, who, body.message_id, f"/v1/manifests/{id}/collect", work)


@router.post("/manifests/{id}/retry-ship", response_model=ManifestOut)
def retry_ship(id: int, db: DB, who: Principal = require("runs:write")):
    """Ship calls that gave up are tried again from the start, with the same
    message_id each, so Simple WMS still ships each delivery once."""
    carriers.get_manifest(db, id)
    db.execute(update(OutboundCall)
               .where(OutboundCall.manifest_id == id, OutboundCall.status == "failed")
               .values(status="pending", attempts=0, last_error=None,
                       next_attempt_at=func.now()))
    return _fresh(db, id)
