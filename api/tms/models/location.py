from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base


class Position(Base):
    """Where a run's phone was, about every 30 seconds from Start until the
    run is finished, and never outside a run. Personal information, not
    business history: the worker deletes each one once it is older than
    TMS_POSITION_RETENTION_DAYS (90 by default). So no append-only trigger,
    and nothing else points at it."""

    __tablename__ = "position"
    __table_args__ = (Index("ix_position_run_id_received_at", "run_id", "received_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"))
    # who was driving and what, as the run had them when it was sent
    driver_id: Mapped[int] = mapped_column(ForeignKey("driver.id"))
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    accuracy_m: Mapped[Decimal] = mapped_column(Numeric(8, 1))
    # when the phone took it (never later than it arrived); what retention counts from
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # when it arrived: "last heard"
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
