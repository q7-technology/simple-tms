from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
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


class OutboundCall(Base):
    """A call to Simple WMS, waiting or made: the ship call when a run
    departs, or when a carrier collects. The worker makes it, retrying after
    1 min, 5, 30 and 2 h; the same message_id goes with every try, so Simple
    WMS acts on it once. It belongs to a run or to a manifest, never both."""

    __tablename__ = "outbound_call"
    __table_args__ = (Index("ix_outbound_call_due", "status", "next_attempt_at"),
                      CheckConstraint("num_nonnulls(run_id, manifest_id) = 1",
                                      name="run_or_manifest"))

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    # ship
    kind: Mapped[str] = mapped_column(String(16))
    # the Simple WMS path, e.g. /v1/deliveries/0080012345/ship, and the body
    path: Mapped[str] = mapped_column(String(200))
    body: Mapped[dict] = mapped_column(JSONB)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), index=True)
    manifest_id: Mapped[int | None] = mapped_column(ForeignKey("manifest.id"), index=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    # pending, done, failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()


class PrintJob(Base):
    """A document for Platen: a template name and JSON, never a rendered
    page. The worker sends it with the same backoff as the ship calls;
    Platen says printed or failed on POST /v1/print-jobs/{job_id}/status. A
    run's run sheet, or a carrier's manifest."""

    __tablename__ = "print_job"
    __table_args__ = (Index("ix_print_job_due", "status", "next_attempt_at"),
                      CheckConstraint("num_nonnulls(run_id, manifest_id) = 1",
                                      name="run_or_manifest"))

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), index=True)
    manifest_id: Mapped[int | None] = mapped_column(ForeignKey("manifest.id"), index=True)
    template: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    printer: Mapped[str] = mapped_column(String(64))
    copies: Mapped[int] = mapped_column(Integer, default=1)
    reference: Mapped[dict] = mapped_column(JSONB, default=dict)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    requested_by: Mapped[str] = mapped_column(String(64))
    # pending, accepted, printed, failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
