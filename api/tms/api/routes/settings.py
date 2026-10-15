"""Settings an admin changes on screen in place of .env's: see
services/runtime_settings.py for which may, and how they take effect."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError
from tms.models import SettingChange
from tms.services import runtime_settings

router = APIRouter(tags=["settings"])


class SettingOut(BaseModel):
    name: str
    env: str
    # screen, secret or file
    kind: str
    # in force now; never a secret's, the database's address or the session key
    value: str | int | float | None
    # .env's, for one that may be changed on screen
    default: str | int | float | None
    is_set: bool
    on_screen: bool
    changed_by: str | None
    changed_at: datetime | None
    why_file_only: str | None


class ChangeOut(BaseModel):
    id: int
    name: str
    env: str
    action: str
    value: str | None
    secret: bool
    changed_by: str
    changed_at: datetime


@router.get("/settings", response_model=list[SettingOut])
def read_settings(db: DB, who: Principal = require("settings:read")):
    return runtime_settings.view(db)


@router.patch("/settings", response_model=list[SettingOut])
def change_settings(db: DB, body: Annotated[dict[str, str | int | float | None], Body()],
                    who: Principal = require("settings:write")):
    """{ "run_sheet_printer": "office", "platen_key": null }: each named
    setting given a value in place of .env's, or null for .env's again. All
    are checked, and kept, together."""
    if not body:
        raise FieldError("body", "name at least one setting to change")
    runtime_settings.change(db, body, by=who.name)
    db.commit()
    runtime_settings.committed()
    return runtime_settings.view(db)


@router.get("/settings/history", response_model=list[ChangeOut])
def settings_history(db: DB, limit: int = Query(default=50, ge=1, le=200),
                     who: Principal = require("settings:read")):
    """Newest first. A secret's value is never in it."""
    rows = db.execute(select(SettingChange).order_by(SettingChange.id.desc()).limit(limit)).scalars()
    return [ChangeOut(id=r.id, name=r.name, env=runtime_settings.env_name(r.name), action=r.action,
                      value=r.value, secret=r.secret, changed_by=r.changed_by,
                      changed_at=r.changed_at) for r in rows]
