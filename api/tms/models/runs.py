from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, and_, text
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from tms.models.access import Driver
from tms.models.base import Base, created_at_column
from tms.models.consignment import Consignment
from tms.models.fleet import Vehicle


class Run(Base):
    """One truck's day: a vehicle, a driver, and stops in the order the
    dispatcher chose. Cancelled, never deleted."""

    __tablename__ = "run"
    __table_args__ = (
        # one live run per truck per day; a cancelled one frees the truck
        Index("uq_run_vehicle_id_day", "vehicle_id", "day", unique=True,
              postgresql_where=text("status <> 'cancelled'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"), index=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("driver.id"), index=True)
    # planned, departed, done (the driver finished it), cancelled
    status: Mapped[str] = mapped_column(String(16), default="planned")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle: Mapped[Vehicle] = relationship()
    driver: Mapped[Driver] = relationship()
    # the stops on it now, in order; ones taken off keep their row
    stops: Mapped[list[Stop]] = relationship(
        primaryjoin=lambda: and_(Run.id == foreign(Stop.run_id), Stop.removed_at.is_(None)),
        order_by=lambda: Stop.sequence, viewonly=True)

    @property
    def number(self) -> str:
        """What the dispatcher, the driver and Simple WMS call it: R-0042."""
        return f"R-{self.id:04d}"


class Stop(Base):
    """A consignment's place on a run. Taken off the run, the row stays with
    removed_at set: cancel, never delete. Once the driver has delivered it or
    could not, `outcome` says which, and the stop stays on its run as a
    record; a consignment that came back can then go on another run."""

    __tablename__ = "stop"
    __table_args__ = (
        # a consignment is on one run at a time, however many people drag it
        Index("uq_stop_consignment_id_live", "consignment_id", unique=True,
              postgresql_where=text("removed_at is null and outcome is null")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    added_by: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = created_at_column()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # delivered, failed: set once, by the driver's confirmation
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    consignment: Mapped[Consignment] = relationship()
