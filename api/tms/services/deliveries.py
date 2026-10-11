"""The driver's side of a run: start it, deliver each stop or say why it
couldn't be, and finish it. Each change locks its run's row first, so two
confirmations for one run (two phones, or a retry racing the original) take
turns, and a stop is recorded once."""
from __future__ import annotations

from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from tms.api.errors import NotFound
from tms.models import Run
from tms.services import runs


def own_run(db: Session, who, run_id: int) -> Run:
    """The run, if the caller may act on it: a driver only on their own (any
    other is not found, so run numbers can't be tried); a key or a user with
    the scope, on any."""
    run = runs.get_run(db, run_id)
    if who.kind == "driver" and run.driver_id != who.id:
        raise NotFound(f"no run {run_id}")
    return run


def driver_runs(db: Session, driver_id: int, day: date) -> list[Run]:
    """This driver's runs for the day, and any of theirs from before that
    left and were never finished."""
    ids = db.execute(
        select(Run.id).where(Run.driver_id == driver_id, or_(
            and_(Run.day == day, Run.status != "cancelled"),
            and_(Run.day < day, Run.status == "departed")))
        .order_by(Run.day, Run.id)).scalars().all()
    return [runs.get_run(db, i) for i in ids]
