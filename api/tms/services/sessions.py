"""Sessions. A desktop user gets a short-lived signed access token plus a
stored, rotating refresh token; a driver gets one signed token for the shift.
HMAC from the standard library, as in Simple WMS."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.config import get_settings
from tms.models import User, UserSession

ACCESS_PREFIX = "tms_s."
ACCESS_TTL_SECONDS = 15 * 60
REFRESH_PREFIX = "tms_r."
REFRESH_TTL = timedelta(days=14)
DRIVER_PREFIX = "tms_d."
DRIVER_TTL_SECONDS = 12 * 3600

# What each desktop role may do.
ROLE_SCOPES: dict[str, list[str]] = {
    "admin": ["*"],
    "manager": ["fleet:read", "fleet:write", "consignments:read"],
    "dispatcher": ["fleet:read", "consignments:read"],
}
ROLES = tuple(ROLE_SCOPES)
# A driver picks a truck after signing in, so may see the fleet. More with step 3.
DRIVER_SCOPES = ["fleet:read"]


def scopes_for(role: str) -> list[str]:
    return list(ROLE_SCOPES.get(role, []))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    key = get_settings().secret_key.encode()
    return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())


def _issue(prefix: str, claims: dict, ttl: int, now: datetime | None) -> str:
    now = now or datetime.now(UTC)
    payload = _b64(json.dumps(
        {**claims, "exp": int((now + timedelta(seconds=ttl)).timestamp()),
         "n": secrets.token_hex(4)},
        separators=(",", ":"),
    ).encode())
    return f"{prefix}{payload}.{_sign(payload)}"


def _read(prefix: str, token: str, now: datetime | None) -> dict | None:
    """The claims, or None if the token is not ours, tampered or expired."""
    if not token.startswith(prefix):
        return None
    try:
        payload, sig = token[len(prefix):].split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig.encode(), _sign(payload).encode()):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    now = now or datetime.now(UTC)
    if int(data.get("exp", 0)) <= int(now.timestamp()):
        return None
    return data


def issue_access_token(user: User, now: datetime | None = None,
                       ttl: int = ACCESS_TTL_SECONDS) -> str:
    return _issue(ACCESS_PREFIX, {"uid": user.id}, ttl, now)


def read_access_token(token: str, now: datetime | None = None) -> int | None:
    data = _read(ACCESS_PREFIX, token, now)
    return int(data["uid"]) if data else None


def issue_driver_token(driver_id: int, now: datetime | None = None,
                       ttl: int = DRIVER_TTL_SECONDS) -> str:
    return _issue(DRIVER_PREFIX, {"did": driver_id}, ttl, now)


def read_driver_token(token: str, now: datetime | None = None) -> int | None:
    data = _read(DRIVER_PREFIX, token, now)
    return int(data["did"]) if data else None


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def start_session(db: Session, user: User) -> tuple[UserSession, str]:
    raw = REFRESH_PREFIX + secrets.token_urlsafe(32)
    row = UserSession(user_id=user.id, refresh_hash=_hash(raw),
                      expires_at=datetime.now(UTC) + REFRESH_TTL)
    db.add(row)
    db.flush()
    return row, raw


def find_session(db: Session, raw_refresh: str) -> UserSession | None:
    row = db.execute(
        select(UserSession).where(UserSession.refresh_hash == _hash(raw_refresh))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        return None
    return row


def rotate_session(db: Session, row: UserSession) -> str:
    """Spend this refresh token and hand out a new one on the same session row."""
    raw = REFRESH_PREFIX + secrets.token_urlsafe(32)
    row.refresh_hash = _hash(raw)
    row.expires_at = datetime.now(UTC) + REFRESH_TTL
    row.last_used_at = datetime.now(UTC)
    db.flush()
    return raw


def revoke_session(db: Session, row: UserSession) -> None:
    row.revoked_at = datetime.now(UTC)
    db.flush()
