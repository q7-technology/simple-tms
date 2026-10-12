from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tms.models.base import Base, Dec, created_at_column


class Vehicle(Base):
    """A truck, van or ute of our own. Deactivated, never deleted."""

    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(primary_key=True)
    rego: Mapped[str] = mapped_column(String(12), unique=True)
    # van, rigid, ute
    type: Mapped[str] = mapped_column(String(8))
    max_kg: Mapped[Dec]
    max_m3: Mapped[Dec]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # how often it is serviced: every so many months, kilometres, or both
    # (whichever comes first); none, and the service is never due
    service_every_months: Mapped[int | None] = mapped_column(Integer)
    service_every_km: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_column()


class ChecklistItem(Base):
    """One line of the pre-start checklist for a type of vehicle. A critical
    item that fails grounds the truck. Changed by retiring the line and
    adding another, never deleted; each check keeps its own copy of what it
    asked."""

    __tablename__ = "checklist_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    # van, rigid, ute
    vehicle_type: Mapped[str] = mapped_column(String(8), index=True)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(80))
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at_column()
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrestartCheck(Base):
    """A driver's pre-start check of the truck for a run, kept as it was sent,
    for good: a trigger refuses UPDATE and DELETE. A critical item failed
    grounds the truck until a fleet manager marks it fixed (a VehicleRecord)."""

    __tablename__ = "prestart_check"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"), index=True)
    # the driver who did it; none when a system sent it with a key
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("driver.id"))
    # when it was done, by the phone's clock (never later than it arrived)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    odometer_km: Mapped[int] = mapped_column(Integer)
    # passed, failed (a non-critical item: the truck may go), grounded
    result: Mapped[str] = mapped_column(String(16))
    recorded_by: Mapped[str] = mapped_column(String(64))
    # when it arrived, taken under the planning lock: what came first
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[PrestartItem]] = relationship(
        order_by="PrestartItem.position", cascade="all", lazy="selectin")


class PrestartItem(Base):
    """One answer on a check, with the item as the checklist had it then.
    A failed item has a note and a photo."""

    __tablename__ = "prestart_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("prestart_check.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    checklist_item_id: Mapped[int] = mapped_column(ForeignKey("checklist_item.id"))
    label: Mapped[str] = mapped_column(String(80))
    critical: Mapped[bool] = mapped_column(Boolean)
    passed: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(String(500))
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pod_file.id"), unique=True)


class VehicleRecord(Base):
    """What a fleet manager records about a truck, each with a note: `fixed`
    (what a failed pre-start found is put right), `service` (done on a day,
    perhaps at a reading), `rego` (the new expiry) and `odometer` (a reading
    put right). Append-only, like the checks: a mistake is put right by a
    newer record, never by changing this one."""

    __tablename__ = "vehicle_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"), index=True)
    # fixed, service, rego, odometer
    kind: Mapped[str] = mapped_column(String(16))
    # fixed: the latest failed check it clears (and every one before it)
    check_id: Mapped[int | None] = mapped_column(ForeignKey("prestart_check.id"))
    done_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    odometer_km: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(500))
    recorded_by: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
