from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from tms.api.errors import Forbidden, Unauthorised
from tms.db import get_sessionmaker
from tms.models import Driver, User
from tms.services import api_keys, sessions


def get_db():
    with get_sessionmaker()() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]


@dataclass(slots=True)
class Principal:
    """Whoever is calling: a desktop user or a driver with a session, or a
    system with an API key."""

    kind: str  # user | driver | key
    id: int
    name: str
    display_name: str
    role: str
    scopes: list[str]
    user: User | None = None
    driver: Driver | None = None

    def has_scope(self, scope: str) -> bool:
        area = scope.split(":")[0]
        return "*" in self.scopes or scope in self.scopes or f"{area}:*" in self.scopes


def get_principal(db: DB, authorization: Annotated[str | None, Header()] = None) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorised("missing bearer token")
    raw = authorization.split(" ", 1)[1].strip()

    if raw.startswith(sessions.ACCESS_PREFIX):
        user_id = sessions.read_access_token(raw)
        user = db.get(User, user_id) if user_id else None
        if (user is None or not user.active
                or not sessions.stamp_matches(raw, user.password_hash)):
            raise Unauthorised("session expired or invalid")
        return Principal(kind="user", id=user.id, name=user.username,
                         display_name=user.display_name, role=user.role,
                         scopes=sessions.scopes_for(user.role), user=user)

    if raw.startswith(sessions.DRIVER_PREFIX):
        driver_id = sessions.read_driver_token(raw)
        driver = db.get(Driver, driver_id) if driver_id else None
        if (driver is None or not driver.active or driver.locked
                or not sessions.stamp_matches(raw, driver.pin_hash)):
            raise Unauthorised("driver session expired or invalid")
        return Principal(kind="driver", id=driver.id, name=driver.code,
                         display_name=driver.name, role="driver",
                         scopes=list(sessions.DRIVER_SCOPES), driver=driver)

    if raw.startswith(api_keys.PREFIX):
        key = api_keys.find(db, raw)
        if key is None:
            raise Unauthorised("unknown or revoked API key")
        return Principal(kind="key", id=key.id, name=key.name, display_name=key.name,
                         role="key", scopes=list(key.scopes))

    raise Unauthorised("not a Simple TMS token")


Who = Annotated[Principal, Depends(get_principal)]


def require(scope: str):
    """Dependency: the caller must carry this scope."""

    def _check(who: Who) -> Principal:
        if not who.has_scope(scope):
            raise Forbidden(f"you do not have the {scope} scope")
        return who

    return Depends(_check)
