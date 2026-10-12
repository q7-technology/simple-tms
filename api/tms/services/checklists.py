"""The pre-start checklist for each type of vehicle. A fleet manager may
change it; a line that goes is retired, never deleted, and a line kept as it
was keeps its id, so a check a phone is still sending finds it. Each check
keeps its own copy of the lines it answered."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.models import ChecklistItem
from tms.services.runs import lock_planning

TYPES = ("van", "rigid", "ute")


def live(db: Session, vehicle_type: str) -> list[ChecklistItem]:
    return list(db.execute(
        select(ChecklistItem).where(ChecklistItem.vehicle_type == vehicle_type,
                                    ChecklistItem.retired_at.is_(None))
        .order_by(ChecklistItem.position, ChecklistItem.id)).scalars())


def replace(db: Session, vehicle_type: str, lines: list[tuple[str, bool]]) -> list[ChecklistItem]:
    """The list as given, in that order. A line with the same label and
    critical flag as before is the same line; the rest are retired."""
    # one change at a time, and a check being recorded sees one list or the other
    lock_planning(db)
    kept = {(i.label, i.critical): i for i in live(db, vehicle_type)}
    out = []
    for position, (label, critical) in enumerate(lines, start=1):
        row = kept.pop((label, critical), None)
        if row is None:
            row = ChecklistItem(vehicle_type=vehicle_type, label=label, critical=critical)
            db.add(row)
        row.position = position
        out.append(row)
    now = datetime.now(UTC)
    for row in kept.values():
        row.retired_at = now
    db.flush()
    return out
