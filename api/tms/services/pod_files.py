"""Signatures and photos for proof of delivery. Kept on disk under
TMS_POD_DIR, one file each, named by the id the phone gave it; the row says
what it is and who sent it. Only PNG and JPEG, checked by their first bytes,
not by what the request says they are."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, FieldError, Refused
from tms.config import get_settings
from tms.models import PodFile
from tms.services.envelope import sender_of

FILE_LOCK = 7303
# what each kind may be, and how big. A phone's photo is made smaller on the
# phone first (about 300 kB); a signature is a few kB.
KINDS = {
    "signature": ({"image/png"}, 1_000_000),
    "photo": ({"image/jpeg", "image/png"}, 5_000_000),
}
LARGEST = max(limit for _, limit in KINDS.values())
MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
EXT = {"image/png": "png", "image/jpeg": "jpg"}


def sniff(data: bytes) -> str | None:
    for content_type, magic in MAGIC.items():
        if data.startswith(magic):
            return content_type
    return None


def full_path(row: PodFile) -> Path:
    return Path(get_settings().pod_dir) / row.path


def _write(target: Path, data: bytes) -> None:
    """All of it or none of it: a half-written file never has the real name."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def store(db: Session, who, file_id: uuid.UUID, kind: str, data: bytes) -> tuple[PodFile, bool]:
    """Keep the file; (row, True) when it is new. The same bytes sent again
    by the same sender are the same file (False); anything else with that id
    is refused."""
    if kind not in KINDS:
        raise FieldError("query.kind", "must be signature or photo")
    allowed, limit = KINDS[kind]
    if len(data) > limit:
        raise Refused(413, "too_large", f"a {kind} may be {limit // 1_000_000} MB at most")
    content_type = sniff(data)
    if content_type not in allowed:
        wanted = " or ".join(sorted(EXT[t].upper() for t in allowed))
        raise Refused(415, "unsupported_type", f"a {kind} must be a {wanted} picture")
    digest = hashlib.sha256(data).hexdigest()

    # two copies of one upload take turns, so neither writes over the other
    db.execute(text("select pg_advisory_xact_lock(:a, :b)"),
               {"a": FILE_LOCK, "b": int.from_bytes(file_id.bytes[:4], "big", signed=True)})
    row = db.get(PodFile, file_id, populate_existing=True)
    if row is not None:
        if row.uploaded_by != sender_of(who) or row.sha256 != digest or row.kind != kind:
            raise Conflict("file_id_taken", f"file {file_id} is already something else")
        return row, False

    relative = f"{file_id.hex[:2]}/{file_id}.{EXT[content_type]}"
    _write(Path(get_settings().pod_dir) / relative, data)
    row = PodFile(id=file_id, kind=kind, content_type=content_type, size_bytes=len(data),
                  sha256=digest, path=relative, uploaded_by=sender_of(who))
    db.add(row)
    db.flush()
    return row, True
