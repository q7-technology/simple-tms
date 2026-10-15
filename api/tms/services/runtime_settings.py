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
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tms.api.errors import FieldErrors
from tms.config import Settings, file_settings, forget_settings
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
# An address and the key that is sent to it, kept as a pair: the key in force
# was always given for the address in force. Both are .env's, or the key was
# given here with the address or after it. So a new address needs its key
# given again with it; putting the address back to .env's puts the key back
# too (unless a new key is given with it); and the key alone goes back to
# .env's only while .env's address is in force.
KEY_FOR = {"wms_url": "wms_api_key", "platen_url": "platen_key"}
NAMES = {"wms_url": "Simple WMS", "platen_url": "Platen"}
MIN_WEBHOOK_SECRET, MAX_WEBHOOK_SECRET = 16, 128
# two admins saving at once take turns
SETTINGS_LOCK = 7_302_202


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


def _problems(e: ValidationError, names) -> list[dict]:
    out = []
    for err in e.errors():
        field = str(err["loc"][0]) if err.get("loc") else "body"
        msg = err["msg"].removeprefix("Value error, ")
        out.append({"field": field if field in names else "body", "message": msg})
    return out


def change(db: Session, changes: dict[str, object], by: str) -> None:
    """Each named setting given a value (kept in place of .env's) or None
    (.env's again), checked together with config.py's rules: all of them, or
    none (422 with a field error on each that is wrong)."""
    base = file_settings()
    errors = []
    for name in changes:
        if name in FILE_ONLY:
            errors.append({"field": name, "message": f"{env_name(name)} is set in .env only: "
                                                     f"{FILE_ONLY[name]}"})
        elif kind(name) == "file":
            errors.append({"field": name, "message": "not a setting Simple TMS has"})
    if errors:
        raise FieldErrors(errors)

    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": SETTINGS_LOCK})
    rows = {r.name: r for r in db.execute(select(Setting)).scalars()}
    wanted = _stored((n, r.value) for n, r in rows.items())
    before = dict(wanted)
    changes = dict(changes)
    for address, key in KEY_FOR.items():
        # the address back to .env's: its key given here goes back with it
        if address in changes and changes[address] is None and address in before \
                and key not in changes and key in before:
            changes[key] = None
    for name, value in changes.items():
        if value is None:
            wanted.pop(name, None)
            continue
        typed = str(value).strip()
        if name in SECRETS and not typed:
            errors.append({"field": name, "message": "Type the new value, or use .env's"})
        elif name == "wms_webhook_secret" and not (MIN_WEBHOOK_SECRET <= len(typed) <= MAX_WEBHOOK_SECRET):
            errors.append({"field": name, "message": f"{MIN_WEBHOOK_SECRET} to {MAX_WEBHOOK_SECRET} "
                                                     "characters, the same as Simple WMS's"})
        wanted[name] = typed
    for address, key in KEY_FOR.items():
        new = changes.get(address)
        was = before.get(address, getattr(base, address)).strip()
        if new is not None and str(new).strip() != was and changes.get(key) is None:
            errors.append({"field": key, "message": f"Type the {NAMES[address]} key again with a new "
                                                    "address: a key on file is only sent where it was given for"})
        elif key in changes and changes[key] is None and \
                wanted.get(address, getattr(base, address)).strip() != getattr(base, address):
            errors.append({"field": key, "message": f"Reset the {NAMES[address]} address too, or give a "
                                                    "new key: .env's key only goes to .env's address."})
    try:
        checked = Settings.model_validate({**base.model_dump(), **wanted})
    except ValidationError as e:
        errors += _problems(e, set(changes))
    if errors:
        raise FieldErrors(errors)

    now = datetime.now(UTC)
    for name, value in changes.items():
        kept = None if value is None else str(getattr(checked, name))
        row = rows.get(name)
        if row is None:
            row = Setting(name=name)
            db.add(row)
        row.value, row.changed_by, row.changed_at = kept, by, now
        secret = name in SECRETS
        db.add(SettingChange(name=name, action="reset" if kept is None else "set",
                             value=None if secret else kept, secret=secret, changed_by=by,
                             changed_at=now))
    db.flush()


def committed() -> None:
    """After a change is committed: this process uses it at once (the
    others within config.REFRESH_SECONDS)."""
    forget_settings()


def view(db: Session) -> list[dict]:
    """Every TMS_* setting, as the Settings drawer shows it. A secret's
    value, and the database's address and session key, are never in it."""
    base = file_settings()
    now = load(base) or base
    rows = {r.name: r for r in db.execute(select(Setting)).scalars()}
    out = []
    for name in (*ON_SCREEN, *SECRETS, *FILE_ONLY):
        row = rows.get(name)
        on_screen = row is not None and row.value is not None
        k = kind(name)
        hidden = k == "secret" or name in ("database_url", "secret_key")
        out.append({
            "name": name, "env": env_name(name), "kind": k,
            "value": None if hidden else getattr(now, name),
            "default": None if hidden or k == "file" else getattr(base, name),
            "is_set": bool(str(getattr(now, name)).strip()),
            "on_screen": on_screen,
            "changed_by": row.changed_by if row else None,
            "changed_at": row.changed_at if row else None,
            "why_file_only": FILE_ONLY.get(name),
        })
    return out
