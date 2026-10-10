from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class InboundEvent(Base):
    """Every Simple WMS event id we have acted on or chosen to ignore. A
    second delivery of the same id finds its row here and does nothing."""

    __tablename__ = "inbound_event"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    external_ref: Mapped[str | None] = mapped_column(String(64))
    # created, ignored
    outcome: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[datetime] = created_at_column()
