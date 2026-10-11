"""How full a run is: weight and volume against the truck's limits. Decimals
all the way. A package with no weight, or without all three sides, adds
nothing and is counted, so the screen can say so."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

# nearly full from here: amber on the plan, "small drops only"
NEAR = Decimal("0.9")
CM3_PER_M3 = Decimal(1_000_000)
KG = Decimal(1)
M3 = Decimal("0.1")


@dataclass(frozen=True)
class Totals:
    weight_kg: Decimal
    volume_m3: Decimal
    packages: int
    cartons: int
    unknown_weight: int
    unknown_volume: int


@dataclass(frozen=True)
class Load:
    weight_kg: Decimal
    volume_m3: Decimal
    max_kg: Decimal
    max_m3: Decimal
    unknown_weight: int
    unknown_volume: int
    state: str  # ok, near, over
    message: str | None


def consignment_totals(packages: Iterable) -> Totals:
    weight = volume = Decimal(0)
    count = cartons = no_weight = no_size = 0
    for p in packages:
        count += 1
        cartons += p.type == "carton"
        if p.weight_kg is None:
            no_weight += 1
        else:
            weight += p.weight_kg
        if p.length_cm is None or p.width_cm is None or p.height_cm is None:
            no_size += 1
        else:
            volume += p.length_cm * p.width_cm * p.height_cm / CM3_PER_M3
    return Totals(weight, volume, count, cartons, no_weight, no_size)


def total(parts: Iterable[Totals]) -> Totals:
    out = Totals(Decimal(0), Decimal(0), 0, 0, 0, 0)
    for t in parts:
        out = Totals(out.weight_kg + t.weight_kg, out.volume_m3 + t.volume_m3,
                     out.packages + t.packages, out.cartons + t.cartons,
                     out.unknown_weight + t.unknown_weight, out.unknown_volume + t.unknown_volume)
    return out


def plain(d: Decimal) -> str:
    """For people: 2380.500000 -> "2,380.5". Never an exponent."""
    text = format(d.normalize(), "f") if d else "0"
    whole, _, frac = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    return f"{sign}{int(whole.lstrip('-')):,}" + (f".{frac}" if frac else "")


def _up(d: Decimal, step: Decimal) -> str:
    return plain(d.quantize(step, rounding=ROUND_CEILING))


def _down(d: Decimal, step: Decimal) -> str:
    return plain(d.quantize(step, rounding=ROUND_FLOOR))


def load(t: Totals, max_kg: Decimal, max_m3: Decimal) -> Load:
    """Over either limit is over; 90 % or more of either is nearly full. What is
    over rounds up, so moving that much is enough; what is left rounds down."""
    over_kg, over_m3 = t.weight_kg - max_kg, t.volume_m3 - max_m3
    if over_kg > 0 or over_m3 > 0:
        parts = []
        if over_kg > 0:
            parts.append(f"{_up(over_kg, KG)} kg too heavy")
        if over_m3 > 0:
            parts.append(f"{_up(over_m3, M3)} m³ too big")
        state = "over"
        message = (" and ".join(parts)
                   + ". Move a stop to another run before printing the run sheet.")
    elif t.weight_kg >= max_kg * NEAR or t.volume_m3 >= max_m3 * NEAR:
        state = "near"
        message = (f"{_down(-over_kg, KG)} kg and {_down(-over_m3, M3)} m³ left. "
                   "Small drops only.")
    else:
        state, message = "ok", None
    return Load(t.weight_kg, t.volume_m3, max_kg, max_m3, t.unknown_weight, t.unknown_volume,
                state, message)
