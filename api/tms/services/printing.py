"""Run sheets and carrier manifests through Platen. Simple TMS renders
nothing: it queues a template name and JSON, the worker sends it, Platen
prints it and says how it went."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from tms.api.errors import Conflict
from tms.config import get_settings
from tms.models import Manifest, PrintJob, Run
from tms.services import carriers, fleet
from tms.services.load import consignment_totals, plain, total
from tms.services.runs import get_run, lock_planning, run_load

# The Platen page template that prints it: make one with this id and publish it.
RUN_SHEET = "run-sheet"
RUN_SHEET_VERSION = "v1"
MANIFEST = "carrier-manifest"
MANIFEST_VERSION = "v1"


def run_sheet_data(run: Run) -> dict:
    """What the run-sheet template binds to: the run at the top, one line per
    stop (Platen gives a page template a row per entry in `lines`, with the
    rest beside it). Text only, never null, so every binding has something."""
    ld = run_load(run)
    lines = []
    for stop in run.stops:
        c = stop.consignment
        to = c.ship_to or {}
        t = consignment_totals(c.packages)
        lines.append({
            "stop": stop.sequence, "customer": c.customer, "wms_ref": c.wms_ref,
            "address": to.get("address") or "", "suburb": to.get("suburb") or "",
            "postcode": to.get("postcode") or "", "phone": to.get("phone") or "",
            "packages": t.packages, "weight_kg": plain(t.weight_kg),
            "required_by": c.required_by.isoformat() if c.required_by else "",
        })
    return {
        "run_no": run.number, "date": run.day.isoformat(), "vehicle": run.vehicle.rego,
        "vehicle_type": run.vehicle.type, "driver": run.driver.name,
        "driver_code": run.driver.code, "stop_count": len(run.stops),
        "total_kg": plain(ld.weight_kg), "total_m3": plain(ld.volume_m3),
        "max_kg": plain(ld.max_kg), "max_m3": plain(ld.max_m3), "lines": lines,
    }


def _platen_set_up() -> None:
    if not get_settings().platen_url:
        raise Conflict("printing_not_set_up",
                       "Printing is not set up: TMS_PLATEN_URL is empty. Ask whoever looks "
                       "after Simple TMS to connect Platen.")


def queue_run_sheet(db: Session, run_id: int, by: str) -> PrintJob:
    settings = get_settings()
    _platen_set_up()
    # read the run as planning leaves it, so a stop dragged on at the same
    # moment cannot slip an over run past the check
    lock_planning(db)
    run = get_run(db, run_id)
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")
    if run.status == "planned":
        # a sheet for a run its truck can't take would send someone out in it
        fleet.refuse_if_blocked(db, run.vehicle, run.day)
    if not run.stops:
        raise Conflict("run_empty", f"{run.number} has no stops to print")
    ld = run_load(run)
    if ld.state == "over":
        raise Conflict("over_capacity", f"{run.number}: {ld.message}")
    job = PrintJob(job_id=uuid.uuid4(), run_id=run.id, template=RUN_SHEET,
                   version=RUN_SHEET_VERSION, printer=settings.run_sheet_printer, copies=1,
                   reference={"type": "run", "ref": run.number}, data=run_sheet_data(run),
                   requested_by=by, status="pending", attempts=0,
                   next_attempt_at=datetime.now(UTC))
    db.add(job)
    db.flush()
    return job


def manifest_data(m: Manifest) -> dict:
    """What the carrier-manifest template binds to: the carrier and the day
    at the top, one line per consignment with its con note. Text only, never
    null, as for a run sheet."""
    lines = []
    for i, b in enumerate(m.lines, start=1):
        c = b.consignment
        to = c.ship_to or {}
        t = consignment_totals(c.packages)
        lines.append({
            "line": i, "con_note": b.con_note or "", "wms_ref": c.wms_ref, "customer": c.customer,
            "address": to.get("address") or "", "suburb": to.get("suburb") or "",
            "state": to.get("state") or "", "postcode": to.get("postcode") or "",
            "packages": t.packages, "weight_kg": plain(t.weight_kg),
        })
    t = total(consignment_totals(b.consignment.packages) for b in m.lines)
    carrier = m.carrier
    return {
        "manifest_no": m.number, "date": m.day.isoformat(), "carrier": carrier.name,
        "account": carrier.account or "",
        "pickup_time": carrier.pickup_time.strftime("%H:%M") if carrier.pickup_time else "",
        "consignment_count": len(m.lines), "package_count": t.packages,
        "total_kg": plain(t.weight_kg), "lines": lines,
    }


def queue_manifest(db: Session, manifest_id: int, by: str) -> PrintJob:
    """The manifest as it is: the bay before collection, what went once it
    is collected. Each call prints again. Not with a con note missing: the
    carrier's driver signs for each line by it."""
    settings = get_settings()
    _platen_set_up()
    lock_planning(db)
    m = carriers.get_manifest(db, manifest_id)
    if not m.lines:
        raise Conflict("manifest_empty", f"{m.number} has nothing on it to print")
    carriers.refuse_missing_con_notes(m)
    job = PrintJob(job_id=uuid.uuid4(), manifest_id=m.id, template=MANIFEST,
                   version=MANIFEST_VERSION, printer=settings.manifest_printer_or_default,
                   copies=1, reference={"type": "manifest", "ref": m.number},
                   data=manifest_data(m), requested_by=by, status="pending", attempts=0,
                   next_attempt_at=datetime.now(UTC))
    db.add(job)
    db.flush()
    return job


def send(http: httpx.Client, job: PrintJob) -> str | None:
    """Hand one job to Platen's intake. None when it took it; otherwise why
    not, in Platen's own words where it gave some. Never the key."""
    settings = get_settings()
    headers = {"X-TMS-Job-Id": str(job.job_id), "User-Agent": "simple-tms/0.1"}
    if settings.platen_key:
        headers["Authorization"] = f"Bearer {settings.platen_key}"
    body = {"job_id": str(job.job_id), "template": job.template, "version": job.version,
            "printer": job.printer, "copies": job.copies, "reference": job.reference,
            "data": job.data}
    try:
        resp = http.post(settings.platen_url, json=body, headers=headers,
                         timeout=settings.worker_http_timeout_seconds)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"[:500]
    if 200 <= resp.status_code < 300:
        return None
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    said = detail if isinstance(detail, str) else resp.text[:200]
    return f"Platen answered {resp.status_code}: {said}"[:500]
