from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer


def fmt_dec(d: Decimal) -> str:
    """Decimal to a plain string with no trailing zeros: 1200.000000 -> "1200"."""
    return format(d.normalize(), "f") if d else "0"


# Weights and volumes go out as strings, like Simple WMS quantities.
Dec = Annotated[Decimal, PlainSerializer(fmt_dec, return_type=str, when_used="json")]


class Page[T](BaseModel):
    items: list[T]
    total: int
