"""What just happened: the latest of what the office hears, newest first,
read from the records already kept: consignments packed in Simple WMS,
delivered or not, runs leaving and finishing, pre-starts, and trucks marked
fixed. Nothing is written for it, and no position is in it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tms.models import (
    Consignment, ConsignmentEvent, Driver, Pod, PrestartCheck, Run, Stop, Vehicle, VehicleRecord,
)


@dataclass
class Happened:
    # packed, delivered, failed, left, finished, prestart, fixed
    kind: str
    at: datetime
    run: Run | None = None
    rego: str | None = None
    driver: str | None = None
    sequence: int | None = None
    customer: str | None = None
    wms_ref: str | None = None
    reason: str | None = None
    # a pre-start: passed, failed or grounded, and the items that failed
    result: str | None = None
    failed: list[str] = field(default_factory=list)
    by: str | None = None
    note: str | None = None


def _runs(db: Session, ids: set[int]) -> dict[int, Run]:
    if not ids:
        return {}
    return {r.id: r for r in db.execute(select(Run).where(Run.id.in_(ids)).options(
        selectinload(Run.vehicle), selectinload(Run.driver))).scalars()}


def _consignments(db: Session, limit: int) -> list[Happened]:
    rows = db.execute(
        select(ConsignmentEvent, Consignment).join(Consignment)
        .where(ConsignmentEvent.kind.in_(("packed", "delivered", "failed")))
        .order_by(ConsignmentEvent.id.desc()).limit(limit)).all()
    pod_ids = [e.detail.get("pod_id") for e, _ in rows if e.detail.get("pod_id")]
    pods = {p.id: p for p in db.execute(select(Pod).where(Pod.id.in_(pod_ids))).scalars()}
    stops = {s.id: s for s in db.execute(select(Stop).where(
        Stop.id.in_([p.stop_id for p in pods.values()]))).scalars()}
    runs = _runs(db, {p.run_id for p in pods.values()})
    out = []
    for e, c in rows:
        pod = pods.get(e.detail.get("pod_id"))
        run = runs.get(pod.run_id) if pod else None
        out.append(Happened(
            kind=e.kind, at=e.at, run=run, rego=run.vehicle.rego if run else None,
            driver=run.driver.name if run else None,
            sequence=stops[pod.stop_id].sequence if pod else None,
            customer=c.customer, wms_ref=c.wms_ref, reason=pod.reason if pod else None))
    return out


def _trips(db: Session, limit: int) -> list[Happened]:
    out = []
    for kind, column in (("left", Run.departed_at), ("finished", Run.finished_at)):
        rows = db.execute(select(Run).where(column.is_not(None)).order_by(column.desc())
                          .limit(limit).options(selectinload(Run.vehicle),
                                                selectinload(Run.driver))).scalars()
        out += [Happened(kind=kind, at=getattr(r, column.key), run=r, rego=r.vehicle.rego,
                         driver=r.driver.name) for r in rows]
    return out


def _checks(db: Session, limit: int) -> list[Happened]:
    checks = db.execute(select(PrestartCheck).order_by(PrestartCheck.id.desc())
                        .limit(limit).options(selectinload(PrestartCheck.items))).scalars().all()
    trucks = {v.id: v.rego for v in db.execute(select(Vehicle).where(
        Vehicle.id.in_({c.vehicle_id for c in checks}))).scalars()}
    drivers = {d.id: d.name for d in db.execute(select(Driver).where(
        Driver.id.in_({c.driver_id for c in checks if c.driver_id}))).scalars()}
    runs = _runs(db, {c.run_id for c in checks})
    return [Happened(kind="prestart", at=c.recorded_at, run=runs.get(c.run_id),
                     rego=trucks[c.vehicle_id], driver=drivers.get(c.driver_id),
                     result=c.result, failed=[i.label for i in c.items if not i.passed],
                     by=c.recorded_by) for c in checks]


def _fixes(db: Session, limit: int) -> list[Happened]:
    rows = db.execute(select(VehicleRecord, Vehicle.rego).join(Vehicle)
                      .where(VehicleRecord.kind == "fixed")
                      .order_by(VehicleRecord.id.desc()).limit(limit)).all()
    return [Happened(kind="fixed", at=r.recorded_at, rego=rego, by=r.recorded_by, note=r.note)
            for r, rego in rows]


def recent(db: Session, who, limit: int) -> list[Happened]:
    """The newest `limit` of each kind, merged and cut to `limit`. Pre-starts
    and trucks marked fixed are the fleet's records: only for a caller who
    may see the fleet (fleet:read)."""
    items = _consignments(db, limit) + _trips(db, limit)
    if who.has_scope("fleet:read"):
        items += _checks(db, limit) + _fixes(db, limit)
    return sorted(items, key=lambda h: h.at, reverse=True)[:limit]
