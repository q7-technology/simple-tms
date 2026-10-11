"""Drains the queues: ship calls to Simple WMS and print jobs to Platen. Run
as `python -m tms.worker`.

A failed try is tried again after 1 min, 5, 30 and 2 h. The try after the
2 h wait is the last: if it fails too, the row is `failed` and waits for a
person (retry-ship on the run, or print again).
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.config import get_settings
from tms.db import get_sessionmaker
from tms.models import OutboundCall, PrintJob
from tms.services import printing, wms_client

log = logging.getLogger("tms.worker")

BACKOFF = (60, 300, 1800, 7200)


def retry_later(row, now: datetime, error: str) -> None:
    """After a failed try: wait the next step of the backoff, or give up once
    the try after the last wait has failed. attempts has counted this try."""
    row.last_error = error[:500]
    if row.attempts > len(BACKOFF):
        row.status = "failed"
    else:
        row.next_attempt_at = now + timedelta(seconds=BACKOFF[row.attempts - 1])


def _claim(session: Session, model, now: datetime):
    """The next row due, locked so a second worker passes it by. One row per
    transaction, so a slow answer holds up only that row."""
    return session.execute(
        select(model)
        .where(model.status == "pending", model.next_attempt_at <= now)
        .order_by(model.next_attempt_at, model.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def send_ship_calls(session: Session, http: httpx.Client, now: datetime | None = None,
                    limit: int = 50) -> int:
    """Make every ship call that is due. Returns how many were tried."""
    settings = get_settings()
    if not settings.wms_url or not settings.wms_api_key:
        return 0    # they wait, untried, until Simple WMS is set up
    now = now or datetime.now(UTC)
    tried = 0
    while tried < limit and (row := _claim(session, OutboundCall, now)) is not None:
        error = wms_client.ship(http, row.path, row.body)
        row.attempts += 1
        tried += 1
        if error is None:
            row.status = "done"
            row.done_at = now
            row.last_error = None
            log.info("ship call %s (%s) done", row.message_id, row.path)
        else:
            retry_later(row, now, error)
            log.warning("ship call %s (%s) try %d failed%s: %s", row.message_id, row.path,
                        row.attempts, "; given up" if row.status == "failed" else "", error)
        session.commit()
    return tried


def send_print_jobs(session: Session, http: httpx.Client, now: datetime | None = None,
                    limit: int = 50) -> int:
    """Hand every due print job to Platen. Platen answers that it has it, then
    says printed or failed on its own (POST /v1/print-jobs/{job_id}/status)."""
    if not get_settings().platen_url:
        return 0    # they wait until Platen is set up
    now = now or datetime.now(UTC)
    tried = 0
    while tried < limit and (job := _claim(session, PrintJob, now)) is not None:
        error = printing.send(http, job)
        job.attempts += 1
        tried += 1
        if error is None:
            job.status = "accepted"
            job.sent_at = now
            job.last_error = None
            log.info("print job %s (%s) accepted by Platen", job.job_id, job.template)
        else:
            retry_later(job, now, error)
            log.warning("print job %s try %d failed%s: %s", job.job_id, job.attempts,
                        "; given up" if job.status == "failed" else "", error)
        session.commit()
    return tried


def wait_for_schema(session: Session, timeout: float = 120, poll: float = 2) -> None:
    """Block until the api container has run the migrations."""
    from sqlalchemy.exc import ProgrammingError

    deadline = time.monotonic() + timeout
    while True:
        try:
            session.execute(select(OutboundCall.id).limit(1))
            return
        except ProgrammingError:
            session.rollback()
            if time.monotonic() >= deadline:
                raise TimeoutError("outbound_call never appeared; did the migrations run?")
            log.info("waiting for migrations")
            time.sleep(poll)


def run_once(session: Session, http: httpx.Client) -> int:
    return send_ship_calls(session, http) + send_print_jobs(session, http)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    make_session = get_sessionmaker()
    with make_session() as session:
        wait_for_schema(session)
    log.info("worker started, polling every %ss", settings.worker_poll_seconds)
    with httpx.Client() as http:
        while True:
            try:
                with make_session() as session:
                    done = run_once(session, http)
            except Exception:  # keep the loop alive; log and back off
                log.exception("worker loop error")
                done = 0
            if done == 0:
                time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
