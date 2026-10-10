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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tms.config import get_settings
from tms.models import Driver, User, UserSession
from tms.services import access

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


def credential_stamp(secret_hash: str | None) -> str:
    """A keyed fingerprint of a password or PIN hash, carried in every token. A new
    password or PIN gives a new hash, so the tokens issued before stop working."""
    return _sign("credential:" + (secret_hash or ""))[:22]


def stamp_matches(token: str, secret_hash: str | None, now: datetime | None = None) -> bool:
    """True if the token was issued against this password or PIN hash."""
    prefix = DRIVER_PREFIX if token.startswith(DRIVER_PREFIX) else ACCESS_PREFIX
    data = _read(prefix, token, now)
    if not data or not isinstance(data.get("cv"), str):
        return False
    return hmac.compare_digest(data["cv"], credential_stamp(secret_hash))


def issue_access_token(user: User, now: datetime | None = None,
                       ttl: int = ACCESS_TTL_SECONDS) -> str:
    return _issue(ACCESS_PREFIX, {"uid": user.id, "cv": credential_stamp(user.password_hash)},
                  ttl, now)


def read_access_token(token: str, now: datetime | None = None) -> int | None:
    data = _read(ACCESS_PREFIX, token, now)
    return int(data["uid"]) if data else None


def issue_driver_token(driver: Driver, now: datetime | None = None,
                       ttl: int = DRIVER_TTL_SECONDS) -> str:
    return _issue(DRIVER_PREFIX, {"did": driver.id, "cv": credential_stamp(driver.pin_hash)},
                  ttl, now)


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


def change_password(db: Session, user: User, password: str) -> None:
    """Set a new password and end every session signed in with the old one: the
    refresh tokens are revoked here, the access tokens by their stamp."""
    user.password_hash = access.hash_password(password)
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.flush()
