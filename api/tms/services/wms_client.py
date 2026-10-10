"""The calls Simple TMS makes to Simple WMS. Step 1 only reads a delivery."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from tms.config import get_settings


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
