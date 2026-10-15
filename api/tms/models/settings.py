from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class Setting(Base):
    """A setting an admin has given on screen, in place of .env's. One row
    per setting, changed in place like a vehicle's limits; `value` null is
    .env's again (a reset keeps the row: nothing is deleted). Only the
    current value of a secret is kept, here and nowhere else."""

    __tablename__ = "setting"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SettingChange(Base):
    """Who changed a setting, to what, and when: append-only, like a
    truck's records. A secret's new value is never copied here, so an old
    key doesn't linger once it is replaced."""

    __tablename__ = "setting_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    # set (a value given on screen) or reset (back to .env's)
    action: Mapped[str] = mapped_column(String(8))
    # what it was set to; null for a reset, and always for a secret
    value: Mapped[str | None] = mapped_column(Text)
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_by: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = created_at_column()
