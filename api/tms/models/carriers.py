from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Time, and_, func, text
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from tms.models.base import Base, created_at_column
from tms.models.consignment import Consignment


class Carrier(Base):
    """An outside carrier the business books freight with. Booked by hand in
    version one: the dispatcher types or scans the con note. `adapter` says
    how a carrier is booked ("manual"); a carrier's own API, added later, is
    another adapter, with no change to the data. Deactivated, never deleted."""

    __tablename__ = "carrier"
    # one carrier per name, whatever its case
    __table_args__ = (Index("uq_carrier_name_lower", func.lower(text("name")), unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # what Simple WMS is told as the carrier when it collects (at most 64 there)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # the business's account with the carrier, printed on the manifest
    account: Mapped[str | None] = mapped_column(String(40))
    # when it usually calls, by the depot's clock: "Pickup about 3:30 pm"
    pickup_time: Mapped[time | None] = mapped_column(Time)
    adapter: Mapped[str] = mapped_column(String(32), default="manual")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_column()


class Manifest(Base):
    """One carrier's pickup: the consignments waiting on the carrier bay for
    it on a day, then, once it has called, what it took. Open until
    collected; one open manifest per carrier per day, and a carrier that
    calls twice in a day gets a second one."""

    __tablename__ = "manifest"
    __table_args__ = (
        Index("uq_manifest_carrier_id_day_open", "carrier_id", "day", unique=True,
              postgresql_where=text("collected_at is null")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_id: Mapped[int] = mapped_column(ForeignKey("carrier.id"), index=True)
    # the day it is for, by the dispatcher's calendar, as a run's is
    day: Mapped[date] = mapped_column(Date, index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_by: Mapped[str | None] = mapped_column(String(64))

    carrier: Mapped[Carrier] = relationship()
    # the consignments on it now, oldest first; ones taken off keep their row
    lines: Mapped[list[Booking]] = relationship(
        primaryjoin=lambda: and_(Manifest.id == foreign(Booking.manifest_id),
                                 Booking.removed_at.is_(None)),
        order_by=lambda: Booking.id, viewonly=True)

    @property
    def number(self) -> str:
        """What the dispatcher and the carrier's driver call it: M-0042."""
        return f"M-{self.id:04d}"


class Booking(Base):
    """A consignment's place on a carrier's manifest, with its con note once
    it is typed or scanned. Taken off the bay, the row stays with removed_at
    set: cancel, never delete. A con note is put right by setting it again,
    and each one set is a row in the consignment's history. `carrier_id` is
    the manifest's, kept here so a con note is one carrier's once."""

    __tablename__ = "booking"
    __table_args__ = (
        # a consignment is with one carrier at a time, and never on a run too
        # (the planning lock sees to that)
        Index("uq_booking_consignment_id_live", "consignment_id", unique=True,
              postgresql_where=text("removed_at is null")),
        # a con note is one consignment's, with that carrier, for good
        Index("uq_booking_carrier_id_con_note", "carrier_id", "con_note", unique=True,
              postgresql_where=text("removed_at is null and con_note is not null")),
        # and its con notes from bookings taken off or collected, kept for good
        Index("ix_booking_carrier_id_con_note", "carrier_id", "con_note"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    manifest_id: Mapped[int] = mapped_column(ForeignKey("manifest.id"), index=True)
    carrier_id: Mapped[int] = mapped_column(ForeignKey("carrier.id"))
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"))
    con_note: Mapped[str | None] = mapped_column(String(40))
    added_by: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = created_at_column()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    consignment: Mapped[Consignment] = relationship()
