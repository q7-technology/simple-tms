"""Drains the queues: ship calls to Simple WMS and print jobs to Platen. Run
as `python -m tms.worker`.

A failed try is tried again after 1 min, 5, 30 and 2 h. The try after the
2 h wait is the last: if it fails too, the row is `failed` and waits for a
person (retry-ship on the run, or print again).

Once an hour (and when it starts) it also forgets positions older than the
retention period.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from tms.config import get_settings
from tms.db import get_sessionmaker
from tms.models import OutboundCall, Pod, Position, PrintJob
from tms.services import printing, wms_client

log = logging.getLogger("tms.worker")

BACKOFF = (60, 300, 1800, 7200)
FORGET_EVERY_SECONDS = 3600


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


def clock() -> datetime:
    return datetime.now(UTC)


def _ship_one(session: Session, http: httpx.Client, now: datetime | None) -> bool:
    """Make the next ship call that is due, if there is one."""
    row = _claim(session, OutboundCall, now or clock())
    if row is None:
        return False
    answer = wms_client.ship(http, row.path, row.body)
    # after the answer, not before: a slow call would date it in the past
    answered = now or clock()
    row.attempts += 1
    if answer.done:
        row.status = "done"
        row.done_at = answered
        row.last_error = None
        row.already_shipped = answer.already_shipped
        log.info("ship call %s (%s) done%s", row.message_id, row.path,
                 "; Simple WMS had it shipped already" if answer.already_shipped else "")
    else:
        retry_later(row, answered, answer.error)
        log.warning("ship call %s (%s) try %d failed%s: %s", row.message_id, row.path,
                    row.attempts, "; given up" if row.status == "failed" else "", answer.error)
    session.commit()
    return True


def _print_one(session: Session, http: httpx.Client, now: datetime | None) -> bool:
    """Hand the next due print job to Platen, if there is one."""
    job = _claim(session, PrintJob, now or clock())
    if job is None:
        return False
    error = printing.send(http, job)
    answered = now or clock()
    job.attempts += 1
    if error is None:
        job.status = "accepted"
        job.sent_at = answered
        job.last_error = None
        log.info("print job %s (%s) accepted by Platen", job.job_id, job.template)
    else:
        retry_later(job, answered, error)
        log.warning("print job %s try %d failed%s: %s", job.job_id, job.attempts,
                    "; given up" if job.status == "failed" else "", error)
    session.commit()
    return True


def _ships_set_up() -> bool:
    settings = get_settings()
    return bool(settings.wms_url and settings.wms_api_key)


def _printing_set_up() -> bool:
    return bool(get_settings().platen_url)


def send_ship_calls(session: Session, http: httpx.Client, now: datetime | None = None,
                    limit: int = 50) -> int:
    """Make every ship call that is due. Returns how many were tried. Each
    time is taken when it happens unless `now` is given."""
    if not _ships_set_up():
        return 0    # they wait, untried, until Simple WMS is set up
    tried = 0
    while tried < limit and _ship_one(session, http, now):
        tried += 1
    return tried


def send_print_jobs(session: Session, http: httpx.Client, now: datetime | None = None,
                    limit: int = 50) -> int:
    """Hand every due print job to Platen. Platen answers that it has it, then
    says printed or failed on its own (POST /v1/print-jobs/{job_id}/status)."""
    if not _printing_set_up():
        return 0    # they wait until Platen is set up
    tried = 0
    while tried < limit and _print_one(session, http, now):
        tried += 1
    return tried


def forget_positions(session: Session, now: datetime | None = None) -> tuple[int, int]:
    """Positions are personal information, not business history: those taken
    longer ago than TMS_POSITION_RETENTION_DAYS are deleted, and the position
    on a proof of delivery is cleared (the delivery itself stays as it was).
    Returns how many positions went, and how many deliveries lost theirs."""
    days = get_settings().position_retention_days
    cutoff = (now or clock()) - timedelta(days=days)
    gone = session.execute(delete(Position).where(Position.recorded_at < cutoff)).rowcount
    cleared = session.execute(
        update(Pod).where(Pod.at < cutoff, Pod.latitude.is_not(None))
        .values(latitude=None, longitude=None, accuracy_m=None)).rowcount
    session.commit()
    # every go, even when nothing was old enough: the log shows it is running
    log.info("forgot %d positions, and where %d deliveries were, older than %d days",
             gone, cleared, days)
    return gone, cleared


def time_to_forget(last: float | None, now: float) -> bool:
    """When it starts (never forgotten yet), then once FORGET_EVERY_SECONDS
    have passed since the last go, whether that went well or not. Times are
    time.monotonic()'s."""
    return last is None or now - last >= FORGET_EVERY_SECONDS


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


def run_once(session: Session, http: httpx.Client, now: datetime | None = None,
             limit: int = 50) -> int:
    """One pass over both queues, a row from each in turn (up to `limit`
    each), so a run's worth of ship calls does not hold up a run sheet that
    a driver is waiting on, nor the other way round. Returns how many were
    tried."""
    ships, prints = _ships_set_up(), _printing_set_up()
    tried = {"ship": 0, "print": 0}
    while ships or prints:
        if ships:
            ships = tried["ship"] < limit and _ship_one(session, http, now)
            tried["ship"] += ships
        if prints:
            prints = tried["print"] < limit and _print_one(session, http, now)
            tried["print"] += prints
    return tried["ship"] + tried["print"]


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    make_session = get_sessionmaker()
    with make_session() as session:
        wait_for_schema(session)
    log.info("worker started, polling every %ss", settings.worker_poll_seconds)
    forgot_at: float | None = None
    with httpx.Client() as http:
        while True:
            if time_to_forget(forgot_at, time.monotonic()):
                try:
                    with make_session() as session:
                        forget_positions(session)
                except Exception:  # tried again in an hour; the queues go on
                    log.exception("forgetting old positions failed")
                forgot_at = time.monotonic()
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
