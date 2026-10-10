from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

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
    created_at: Mapped[datetime] = created_at_column()
