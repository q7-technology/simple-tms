"""Run sheets, and Platen telling us how each one went."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import NotFound
from tms.models import PrintJob
from tms.services import printing

router = APIRouter(tags=["printing"])


class PrintJobOut(BaseModel):
    job_id: uuid.UUID
    run_id: int
    template: str
    printer: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
    printed_at: datetime | None


class JobStatusIn(BaseModel):
    """What Platen sends, the same body Simple WMS takes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["accepted", "printed", "failed"]
    message: str | None = Field(default=None, max_length=500)
    printer: str | None = Field(default=None, max_length=64)


def job_out(job: PrintJob) -> PrintJobOut:
    return PrintJobOut(job_id=job.job_id, run_id=job.run_id, template=job.template,
                       printer=job.printer, status=job.status, attempts=job.attempts,
                       last_error=job.last_error, created_at=job.created_at,
                       sent_at=job.sent_at, printed_at=job.printed_at)


@router.post("/runs/{id}/print", status_code=202, response_model=PrintJobOut)
def print_run_sheet(id: int, db: DB, who: Principal = require("runs:write")):
    """Queue the run sheet for Platen. Each call is a new print."""
    job = printing.queue_run_sheet(db, id, by=who.name)
    db.commit()
    return job_out(job)


@router.post("/print-jobs/{job_id}/status", response_model=PrintJobOut)
def job_status(job_id: uuid.UUID, body: JobStatusIn, db: DB,
               who: Principal = require("printing:write")):
    """Platen says how a job went, with a key that has printing:write. A late
    `accepted` does not undo `printed` or `failed`."""
    job = db.execute(select(PrintJob).where(PrintJob.job_id == job_id)
                     .with_for_update()).scalar_one_or_none()
    if job is None:
        raise NotFound(f"no print job {job_id}")
    if not (body.status == "accepted" and job.status in ("printed", "failed")):
        job.status = body.status
    job.printer = body.printer or job.printer
    if body.status == "printed":
        job.printed_at = datetime.now(UTC)
        job.last_error = None
    elif body.status == "failed":
        job.last_error = body.message or "Platen said it failed"
    db.commit()
    return job_out(job)
