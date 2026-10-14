"""How a consignment is booked with a carrier, and later followed.

Version one books by hand: the dispatcher books with the carrier as they
always have, then types or scans the con note into Simple TMS. That is the
"manual" adapter, and every carrier has it.

A carrier's own API comes later as another adapter, named in `carrier.adapter`.
It changes nothing in the data: a booking's con note is set by the adapter
instead of a person (its history row says `by: "adapter:<name>"`), and what
it hears back from tracking becomes rows in the consignment's history. Its
calls go through the worker's queue (an `outbound_call` of their own kind)
with the same backoff as the ship calls, never inside a request, so a slow
carrier never holds the planning lock or the dispatcher's screen."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Adapter(Protocol):
    def book(self, carrier, consignment) -> str | None:
        """Book the consignment with the carrier: its con note, or None when
        a person books it and types the con note."""

    def track(self, carrier, con_note: str) -> list[dict]:
        """What the carrier says has happened to it since it was collected,
        oldest first: [{"at", "kind", "detail"}]. None known: []."""


class Manual:
    """Booked by hand: nothing to call, and nothing to hear back."""

    def book(self, carrier, consignment) -> str | None:
        return None

    def track(self, carrier, con_note: str) -> list[dict]:
        return []


# what `carrier.adapter` may name
ADAPTERS: dict[str, Adapter] = {"manual": Manual()}
