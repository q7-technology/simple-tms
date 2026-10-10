"""The calls Simple TMS makes to Simple WMS. Step 1 only reads a delivery."""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tms.config import get_settings


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
    if resp.status_code != 200:
        raise WmsUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        delivery = resp.json()
    except ValueError:
        delivery = None
    if not isinstance(delivery, dict):
        raise WmsUnavailable(f"the answer was not a delivery: {resp.text[:200]}")
    return delivery
