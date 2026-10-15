"""The calls Simple TMS makes to Simple WMS: read a delivery when it is packed,
say it shipped when its run departs or a carrier collects it, and say it came
back when its run is finished without delivering it."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tms.config import get_settings

log = logging.getLogger("tms.wms")

# Answers that will not mend themselves on a retry: someone has to fix the key
# or the subscriber. Never put the key itself in these.
_REFUSALS = {
    401: "Simple WMS does not know our API key: check TMS_WMS_API_KEY is a live "
         "Simple WMS key",
    403: "Simple WMS will not let our API key read this delivery: the key needs the "
         "tasks:read scope, and its warehouses and owner must cover the delivery",
    404: "Simple WMS has no such delivery for that owner: check the event's owner is the "
         "delivery's, and the key is not limited to another owner",
}


class WmsDelivery(BaseModel):
    """The parts of Simple WMS's GET /v1/deliveries/{ref} (its DeliveryOut) that
    Simple TMS keeps. An answer that does not fit is refused, not guessed at."""

    model_config = ConfigDict(extra="ignore")
    warehouse: str | None = Field(default=None, max_length=32)
    required_by: date | None = None
    ship_to: dict

    @field_validator("ship_to")
    @classmethod
    def _name_is_text(cls, ship_to: dict) -> dict:
        name = ship_to.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError("its name should be text")
        return ship_to

    def customer(self, ref: str) -> str:
        """The ship-to's name, cut to fit; the ref when there is none."""
        return (self.ship_to.get("name") or ref)[:200]


class WmsUnavailable(Exception):
    """Simple WMS could not be asked, or did not answer with the delivery."""


def get_delivery(ref: str, owner: str) -> dict:
    """GET /v1/deliveries/{ref}?owner=... with our WMS key (scope tasks:read)."""
    settings = get_settings()
    if not settings.wms_url or not settings.wms_api_key:
        raise WmsUnavailable("TMS_WMS_URL and TMS_WMS_API_KEY are not set")
    url = f"{settings.wms_url.rstrip('/')}/v1/deliveries/{quote(ref, safe='')}"
    try:
        resp = httpx.get(url, params={"owner": owner},
                         headers={"Authorization": f"Bearer {settings.wms_api_key}",
                                  "User-Agent": "simple-tms/0.1"},
                         timeout=settings.wms_timeout_seconds)
    except httpx.HTTPError as exc:
        raise WmsUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc
    if resp.status_code in _REFUSALS:
        log.error("HTTP %s reading delivery %s (owner %s). %s",
                  resp.status_code, ref, owner, _REFUSALS[resp.status_code])
    if resp.status_code != 200:
        raise WmsUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        delivery = resp.json()
    except ValueError:
        delivery = None
    if not isinstance(delivery, dict):
        raise WmsUnavailable(f"the answer was not a delivery: {resp.text[:200]}")
    return delivery


# A ship call refused like this will not mend itself either; it is retried
# anyway, so it goes through once someone fixes the key, and logged as an error.
_SHIP_REFUSALS = {
    401: "Simple WMS does not know our API key: check TMS_WMS_API_KEY is a live "
         "Simple WMS key",
    403: "Simple WMS will not let our API key ship this delivery: the key needs the "
         "tasks:write scope, and its warehouses and owner must cover the delivery",
    404: "Simple WMS has no such delivery for that owner",
}


@dataclass(frozen=True)
class ShipAnswer:
    """How a call went. `done`: Simple WMS took it, or says it had it already
    (`already_shipped`, `already_returned`), which no retry will change.
    Otherwise `error` says why not, for last_error; never the key."""
    done: bool
    already_shipped: bool = False
    error: str | None = None


# Simple WMS had it already: a retry after a reply that was lost, once Simple
# WMS has stopped remembering the message_id (24 hours). The calls go in
# order, so it can only be this call's own work.
_ALREADY = {"already_shipped", "already_returned"}


def ship(http: httpx.Client, path: str, body: dict, kind: str = "ship") -> ShipAnswer:
    """Make one call, a ship or a return (`kind`): POST {TMS_WMS_URL}{path}
    with our key. A `409 already_shipped` or `409 already_returned` is done
    too."""
    settings = get_settings()
    url = settings.wms_url.rstrip("/") + path
    try:
        resp = http.post(url, json=body,
                         headers={"Authorization": f"Bearer {settings.wms_api_key}",
                                  "User-Agent": "simple-tms/0.1"},
                         timeout=settings.worker_http_timeout_seconds)
    except httpx.HTTPError as exc:
        return ShipAnswer(done=False, error=f"{type(exc).__name__}: {exc}"[:500])
    if 200 <= resp.status_code < 300:
        return ShipAnswer(done=True)
    try:
        code = resp.json().get("code")
    except (ValueError, AttributeError):
        code = None
    if resp.status_code == 409 and code in _ALREADY:
        log.info("%s: Simple WMS says %s; taken as done", path, code)
        return ShipAnswer(done=True, already_shipped=code == "already_shipped")
    if resp.status_code in _SHIP_REFUSALS:
        why = _SHIP_REFUSALS[resp.status_code]
        if resp.status_code == 404 and kind == "return":
            why += ", or this Simple WMS has no return endpoint yet"
        log.error("HTTP %s on %s. %s", resp.status_code, path, why)
    return ShipAnswer(done=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}")
