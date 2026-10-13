from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class InboundMessage(Base):
    """Every confirmation from the driver app, by its message_id, with the
    reply it got. A repeat finds its row here and gets the same reply back,
    as Simple WMS's inbound envelope does. Kept for good: a phone can sit in
    a black spot for a whole shift."""

    __tablename__ = "inbound_message"

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # who sent it: driver:7, key:2, user:1
    sender: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = created_at_column()
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    last_duplicate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PodFile(Base):
    """A signature or photo, kept on disk under TMS_POD_DIR. The id is the
    phone's, chosen when the picture was taken, so sending it again is
    harmless. The pod that names it is what it belongs to; a file goes with
    one pod at most."""

    __tablename__ = "pod_file"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # signature, photo
    kind: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # under TMS_POD_DIR: 3f/3f2a…-….png (the first two hex digits of the id)
    path: Mapped[str] = mapped_column(String(200))
    # who sent it, as for inbound_message: only they may name it in a pod
    uploaded_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = created_at_column()


class Pod(Base):
    """What happened at a stop, once: delivered (with the signature, the
    printed name and a photo, as the customer needs) or failed (with the
    reason). One per stop; the API never changes one."""

    __tablename__ = "pod"
    # the worker clears a position older than the retention period: this finds them
    __table_args__ = (Index("ix_pod_at_located", "at",
                            postgresql_where=text("latitude is not null")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    stop_id: Mapped[int] = mapped_column(ForeignKey("stop.id"), unique=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    # delivered, failed
    outcome: Mapped[str] = mapped_column(String(16))
    # failed: closed, refused, damaged, wrong_address
    reason: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(500))
    signed_name: Mapped[str | None] = mapped_column(String(120))
    signature_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pod_file.id"), unique=True)
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pod_file.id"), unique=True)
    # each package, and whether it was scanned or ticked: [{package_no, how}]
    packages: Mapped[list] = mapped_column(JSONB, default=list)
    # when it happened, by the phone's clock (never later than it arrived)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # where the phone was, if the driver let it say; cleared after the
    # retention period, as positions are (the only change ever made to a pod)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    recorded_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
