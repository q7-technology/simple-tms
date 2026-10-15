from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tms.models.base import Base, DecOpt, created_at_column


class Consignment(Base):
    """One delivery to one place, with its packages. Keeps the Simple WMS
    reference so the ship call in step 2 can name it."""

    __tablename__ = "consignment"
    __table_args__ = (UniqueConstraint("owner", "wms_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # the delivery's external_ref in Simple WMS
    wms_ref: Mapped[str] = mapped_column(String(64))
    warehouse: Mapped[str] = mapped_column(String(32))
    owner: Mapped[str] = mapped_column(String(32))
    customer: Mapped[str] = mapped_column(String(200))
    ship_to: Mapped[dict] = mapped_column(JSONB, default=dict)
    required_by: Mapped[date | None] = mapped_column(Date)
    # packed (in the tray), assigned (on a planned run), departed, delivered,
    # failed (came back: in the tray again), booked (on the carrier bay),
    # collected (the carrier has it)
    status: Mapped[str] = mapped_column(String(16), default="packed", index=True)
    created_at: Mapped[datetime] = created_at_column()

    packages: Mapped[list[Package]] = relationship(
        back_populates="consignment", cascade="all, delete-orphan", order_by="Package.package_no")
    events: Mapped[list[ConsignmentEvent]] = relationship(
        back_populates="consignment", order_by="ConsignmentEvent.id")


class Package(Base):
    """A carton, pallet, tote or satchel, as Simple WMS packed it."""

    __tablename__ = "package"
    __table_args__ = (UniqueConstraint("consignment_id", "package_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    package_no: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16), default="carton")
    weight_kg: Mapped[DecOpt]
    length_cm: Mapped[DecOpt]
    width_cm: Mapped[DecOpt]
    height_cm: Mapped[DecOpt]
    sscc: Mapped[str | None] = mapped_column(String(18))

    consignment: Mapped[Consignment] = relationship(back_populates="packages")


class ConsignmentEvent(Base):
    """Append-only status history. A trigger refuses UPDATE and DELETE; a
    mistake is corrected by a new row."""

    __tablename__ = "consignment_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    # packed, assigned, unassigned, departed, delivered, failed, returned
    # (off the truck at the depot, Simple WMS to be told), booked, unbooked,
    # con_note, collected
    kind: Mapped[str] = mapped_column(String(16))
    at: Mapped[datetime] = created_at_column()
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)

    consignment: Mapped[Consignment] = relationship(back_populates="events")
