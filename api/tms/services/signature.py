"""Simple WMS signs each event body: X-WMS-Signature: sha256=<hex HMAC-SHA256
of the raw body, keyed with the subscriber's secret>."""
from __future__ import annotations

import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    """True only if header is the signature of exactly these bytes. Compared in
    constant time, so the answer's timing gives nothing away."""
    if not secret or not header:
        return False
    # bytes, so a header with odd characters is a mismatch, not a TypeError
    return hmac.compare_digest(sign(secret, body).encode(), header.strip().encode())
