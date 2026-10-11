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
from tms.models import Consignment
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


class ConsignmentDetail(ConsignmentOut):
    history: list[HistoryOut]


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
    """Newest first. `q` matches the WMS delivery or the customer."""
    query = select(Consignment).options(selectinload(Consignment.packages))
    if status:
        query = query.where(Consignment.status.in_(status.split(",")))
    if warehouse:
        query = query.where(Consignment.warehouse == warehouse)
    if q:
        # autoescape: a % or _ typed in the search box is text, not a wildcard
        query = query.where(or_(Consignment.wms_ref.icontains(q, autoescape=True),
                                Consignment.customer.icontains(q, autoescape=True)))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(
        query.order_by(Consignment.id.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(items=[ConsignmentOut(**consignment_out(c)) for c in rows], total=total)


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
        **consignment_out(c),
        history=[HistoryOut(kind=e.kind, at=e.at, detail=e.detail or {}) for e in c.events],
    )
