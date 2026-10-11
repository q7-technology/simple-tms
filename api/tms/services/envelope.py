"""A confirmation from the driver app is acted on once per message_id. A
repeat gets the original reply back and does nothing, as Simple WMS's
inbound envelope (simple-wms api/wms/api/envelope.py) does."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from tms.api.errors import Conflict
from tms.models import InboundMessage

# the two-number form of an advisory lock: its keys never meet the planning
# lock's single number
MESSAGE_LOCK = 7302


def sender_of(who) -> str:
    return f"{who.kind}:{who.id}"


def lock_key(value: uuid.UUID) -> int:
    """32 bits of the uuid, as Postgres's int4 wants them."""
    return int.from_bytes(value.bytes[:4], "big", signed=True)


def handle(db: Session, who, message_id: uuid.UUID, path: str,
           work: Callable[[], BaseModel], status_code: int = 200) -> JSONResponse:
    """Run `work` once per message_id. Two copies of one message arriving at
    once take turns on a lock, so the second finds the first's reply. The
    reply is stored in the same transaction as the work: both land, or
    neither does and the phone tries again. A refusal is not stored, so the
    same message can be sent again once whatever stopped it is put right.

    `path` names the action and what it is on (`/v1/runs/7/stops/31/deliver`),
    so the same message_id on another action (a start's id sent again on a
    delivery) is a mistake, refused, never answered with the other's reply."""
    db.execute(text("select pg_advisory_xact_lock(:a, :b)"),
               {"a": MESSAGE_LOCK, "b": lock_key(message_id)})
    seen = db.get(InboundMessage, message_id, populate_existing=True)
    if seen is not None:
        if seen.sender != sender_of(who):
            raise Conflict("message_id_taken", "that message_id was sent by someone else")
        if seen.path != path:
            raise Conflict("message_id_reused",
                           "that message_id was already used for another confirmation")
        seen.duplicates += 1
        seen.last_duplicate_at = datetime.now(UTC)
        db.commit()
        return JSONResponse(status_code=seen.status_code, content=seen.response)

    reply = work().model_dump(mode="json")
    db.add(InboundMessage(message_id=message_id, sender=sender_of(who), path=path,
                          status_code=status_code, response=reply, duplicates=0))
    db.commit()
    return JSONResponse(status_code=status_code, content=reply)
