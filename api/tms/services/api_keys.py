"""Keys for systems, not people. The key is shown once; only its SHA-256 is
kept, which is enough for 256 random bits: there is nothing to guess back."""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.models import ApiKey

PREFIX = "tms_k."
# what a key may be given; a person's role gives the rest
SCOPES = ("printing:write", "runs:read", "runs:write", "consignments:read", "fleet:read",
          "deliveries:read", "deliveries:write", "location:read")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(db: Session, name: str, scopes: list[str]) -> tuple[ApiKey, str]:
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown or not scopes:
        raise ValueError(f"scopes must be some of {', '.join(SCOPES)}")
    raw = PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(name=name, key_hash=_hash(raw), scopes=list(scopes), active=True)
    db.add(key)
    db.flush()
    return key, raw


def find(db: Session, raw: str) -> ApiKey | None:
    """The live key this is, noting that it was used."""
    key = db.execute(select(ApiKey).where(ApiKey.key_hash == _hash(raw))).scalar_one_or_none()
    if key is None or not key.active:
        return None
    key.last_used_at = datetime.now(UTC)
    db.commit()
    return key
