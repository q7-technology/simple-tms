"""Consignments, and each one's history."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from tms.api.deps import DB, Principal, require
from tms.api.errors import NotFound
from tms.api.schemas import Dec, Page
from tms.models import Booking, Consignment, Manifest, Run, Stop
from tms.services.load import consignment_totals

router = APIRouter(tags=["consignments"])


class PackageOut(BaseModel):
    package_no: int
    type: str
    weight_kg: Dec | None
    length_cm: Dec | None
    width_cm: Dec | None
    height_cm: Dec | None
    sscc: str | None


class HistoryOut(BaseModel):
    kind: str
    at: datetime
    detail: dict


class OnRun(BaseModel):
    id: int
    run_no: str
    driver: str
    sequence: int


class WithCarrier(BaseModel):
    id: int
    name: str
    con_note: str | None
    manifest_id: int
    manifest_no: str
    collected_at: datetime | None


class ConsignmentOut(BaseModel):
    id: int
    wms_ref: str
    warehouse: str
    owner: str
    customer: str
    ship_to: dict
    required_by: date | None
    status: str
    created_at: datetime
    # the packages added up: what it puts on a truck
    weight_kg: Dec
    volume_m3: Dec
    packages: list[PackageOut]
    # where it is now: the run it is on (or was last on, if it is delivered
    # or came back), or the carrier it is booked with
    run: OnRun | None = None
    carrier: WithCarrier | None = None


class ConsignmentDetail(ConsignmentOut):
    history: list[HistoryOut]


ON_A_RUN = ("assigned", "departed", "delivered", "failed")
WITH_A_CARRIER = ("booked", "collected")


def whereabouts(db, cs: list[Consignment]) -> dict[int, dict]:
    """Each one's run or carrier, read for the whole page at once."""
    ids = [c.id for c in cs]
    out: dict[int, dict] = {i: {} for i in ids}
    if not ids:
        return out
    stops = db.execute(
        select(Stop, Run).join(Run, Run.id == Stop.run_id)
        .where(Stop.consignment_id.in_(ids), Stop.removed_at.is_(None))
        .order_by(Stop.id).options(selectinload(Run.driver))).all()
    for stop, run in stops:     # the latest stop wins
        out[stop.consignment_id]["run"] = OnRun(id=run.id, run_no=run.number,
                                                driver=run.driver.name, sequence=stop.sequence)
    booked = db.execute(
        select(Booking, Manifest).join(Manifest, Manifest.id == Booking.manifest_id)
        .where(Booking.consignment_id.in_(ids), Booking.removed_at.is_(None))
        .options(selectinload(Manifest.carrier))).all()
    for b, m in booked:
        out[b.consignment_id]["carrier"] = WithCarrier(
            id=m.carrier.id, name=m.carrier.name, con_note=b.con_note, manifest_id=m.id,
            manifest_no=m.number, collected_at=m.collected_at)
    for c in cs:
        if c.status not in ON_A_RUN:
            out[c.id].pop("run", None)
        if c.status not in WITH_A_CARRIER:
            out[c.id].pop("carrier", None)
    return out


def consignment_out(c: Consignment) -> dict:
    totals = consignment_totals(c.packages)
    return dict(
        id=c.id, wms_ref=c.wms_ref, warehouse=c.warehouse, owner=c.owner, customer=c.customer,
        ship_to=c.ship_to or {}, required_by=c.required_by, status=c.status,
        created_at=c.created_at, weight_kg=totals.weight_kg, volume_m3=totals.volume_m3,
        packages=[PackageOut(package_no=p.package_no, type=p.type, weight_kg=p.weight_kg,
                             length_cm=p.length_cm, width_cm=p.width_cm, height_cm=p.height_cm,
                             sscc=p.sscc) for p in c.packages],
    )


@router.get("/consignments", response_model=Page[ConsignmentOut])
def list_consignments(
    db: DB, status: str | None = None, warehouse: str | None = None,
    q: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=0, le=2000), offset: int = Query(default=0, ge=0),
    who: Principal = require("consignments:read"),
):
    """Newest first. `q` matches the WMS delivery, the customer or the con note."""
    query = select(Consignment).options(selectinload(Consignment.packages))
    if status:
        query = query.where(Consignment.status.in_(status.split(",")))
    if warehouse:
        query = query.where(Consignment.warehouse == warehouse)
    if q:
        # autoescape: a % or _ typed in the search box is text, not a wildcard
        con_notes = select(Booking.consignment_id).where(
            Booking.removed_at.is_(None), Booking.con_note.icontains(q, autoescape=True))
        query = query.where(or_(Consignment.wms_ref.icontains(q, autoescape=True),
                                Consignment.customer.icontains(q, autoescape=True),
                                Consignment.id.in_(con_notes)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(
        query.order_by(Consignment.id.desc()).limit(limit).offset(offset)
    ).scalars().all()
    where = whereabouts(db, rows)
    return Page(items=[ConsignmentOut(**consignment_out(c), **where[c.id]) for c in rows],
                total=total)


@router.get("/consignments/{id}", response_model=ConsignmentDetail)
def get_consignment(id: int, db: DB, who: Principal = require("consignments:read")):
    c = db.execute(
        select(Consignment)
        .options(selectinload(Consignment.packages), selectinload(Consignment.events))
        .where(Consignment.id == id)
    ).scalar_one_or_none()
    if c is None:
        raise NotFound(f"no consignment {id}")
    return ConsignmentDetail(
        **consignment_out(c), **whereabouts(db, [c])[c.id],
        history=[HistoryOut(kind=e.kind, at=e.at, detail=e.detail or {}) for e in c.events],
    )
