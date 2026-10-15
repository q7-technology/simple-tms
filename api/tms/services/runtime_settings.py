"""Settings an admin may change on screen, in place of .env's.

Every TMS_* setting is one of three kinds:

- **on screen**: safe to change while running, and can't lock anyone out or
  weaken security (printers, the Simple WMS and Platen addresses, waits,
  how long positions are kept, the ETA's first guess);
- **secret**: may be replaced on screen, but is never shown or returned
  again, and never written to the log or the record of changes (the Simple
  WMS key and webhook secret, the Platen key);
- **file only**: could lock everyone out, break the app or weaken security,
  or is read by something other than the API when it starts (the database,
  the session key, where proof-of-delivery files are kept, and the map
  tiles, which Caddy's Content-Security-Policy is built from).

.env gives each its default. A value given on screen is kept in `setting`
(one row each, changed in place) with every change in `setting_change`
(append-only, secrets' values left out), and is checked with config.py's
own rules before it is kept. config.get_settings() reads them back, so the
API and the worker both use them, within REFRESH_SECONDS, without a
restart."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tms.config import Settings
from tms.models import Setting, SettingChange

log = logging.getLogger("tms.settings")

ON_SCREEN = ("wms_url", "wms_timeout_seconds", "platen_url", "run_sheet_printer",
             "manifest_printer", "position_retention_days", "eta_minutes_per_stop",
             "worker_poll_seconds", "worker_http_timeout_seconds")
SECRETS = ("wms_api_key", "wms_webhook_secret", "platen_key")
# why each stays in .env
FILE_ONLY = {
    "database_url": "the API and the worker need it to reach the database at all",
    "secret_key": "it signs every session: a wrong one signs everyone out",
    "pod_dir": "signatures and photos are kept there, in a volume Compose mounts",
    "map_tile_url": "Caddy lets the tiles in through the desktop's Content-Security-Policy, "
                    "from TMS_MAP_TILE_ORIGIN, when it starts",
    "map_tile_origin": "Caddy reads it when it starts, for the desktop's "
                       "Content-Security-Policy",
    "map_attribution": "it is HTML the desktop shows as it is, and belongs with the tile URL",
}


def env_name(name: str) -> str:
    return f"TMS_{name.upper()}"


def kind(name: str) -> str:
    return "secret" if name in SECRETS else "screen" if name in ON_SCREEN else "file"


def _stored(rows) -> dict[str, str]:
    return {n: v for n, v in rows if v is not None and (n in ON_SCREEN or n in SECRETS)}


def load(base: Settings) -> Settings | None:
    """.env's settings with those given on screen in their place. None when
    the database can't be read (not up, or before the migration): the
    caller keeps what it had."""
    from tms.db import get_engine

    try:
        with get_engine().connect() as conn:
            given = _stored(conn.execute(select(Setting.name, Setting.value)).all())
    except SQLAlchemyError:
        return None
    if not given:
        return base
    try:
        return Settings.model_validate({**base.model_dump(), **given})
    except ValidationError:
        # .env changed under them: never say the values (a secret may be one)
        log.warning("settings given on screen don't fit .env any more; .env's are in force "
                    "until an admin saves them again")
        return base
