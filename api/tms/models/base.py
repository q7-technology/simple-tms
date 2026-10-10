from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Weights, volumes and dimensions are decimals. Never floats.
Dec = Annotated[Decimal, mapped_column(Numeric(18, 6))]
DecOpt = Annotated[Decimal | None, mapped_column(Numeric(18, 6))]

naming = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming)


def created_at_column():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
