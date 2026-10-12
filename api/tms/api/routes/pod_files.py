"""Signatures and photos for proof of delivery: sent by the phone before the
confirmation that names them, and shown only to who may see them."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tms.api.deps import DB, Principal, Who, require
from tms.api.errors import NotFound, Refused
from tms.models import PodFile
from tms.services import pod_files
from tms.services.envelope import sender_of

router = APIRouter(tags=["deliveries"])
log = logging.getLogger("tms.pod")


class PodFileOut(BaseModel):
    id: uuid.UUID
    kind: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


def file_out(row: PodFile) -> PodFileOut:
    return PodFileOut(id=row.id, kind=row.kind, content_type=row.content_type,
                      size_bytes=row.size_bytes, sha256=row.sha256, created_at=row.created_at)


@dataclass(frozen=True)
class Upload:
    kind: str
    data: bytes


async def picture(request: Request, kind: Literal["signature", "photo"] = Query()) -> Upload:
    """The kind, then the request body, read no further than the largest file
    we keep. The kind is checked here, so a wrong one is refused before a byte
    of the body is read (a dependency with an invalid parameter isn't run)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > pod_files.LARGEST:
        raise Refused(413, "too_large", "that file is too big")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > pod_files.LARGEST:
            raise Refused(413, "too_large", "that file is too big")
    return Upload(kind=kind, data=bytes(data))


@router.put("/pod-files/{file_id}", response_model=PodFileOut, status_code=201)
def put_file(file_id: uuid.UUID, db: DB,
             who: Principal = require("deliveries:write"), upload: Upload = Depends(picture)):
    """The picture as the body (Content-Type image/png or image/jpeg; what
    counts is its first bytes). 201 when new; the same bytes again, 200. The
    caller and the kind are checked before the body is read (dependencies run
    in order)."""
    row, new = pod_files.store(db, who, file_id, upload.kind, upload.data)
    db.commit()
    return JSONResponse(status_code=201 if new else 200,
                        content=file_out(row).model_dump(mode="json"))


@router.get("/pod-files/{file_id}", response_class=Response)
def get_file(file_id: uuid.UUID, db: DB, who: Who):
    """For anyone who may read consignments, and for whoever sent it."""
    row = db.get(PodFile, file_id)
    if row is None or not (who.has_scope("consignments:read")
                           or row.uploaded_by == sender_of(who)):
        raise NotFound(f"no file {file_id}")
    try:
        content = pod_files.full_path(row).read_bytes()
    except FileNotFoundError:
        # the row is there but the picture is not: a volume lost or restored
        # without it. Not found to the caller; someone should look.
        log.warning("pod file %s is recorded but missing from disk at %s",
                    file_id, pod_files.full_path(row))
        raise NotFound(f"no file {file_id}") from None
    return Response(content=content, media_type=row.content_type,
                    headers={"Cache-Control": "private, no-store",
                             # opened on its own, it runs nothing and reaches nothing
                             "Content-Security-Policy": "default-src 'none'; sandbox",
                             "Content-Disposition":
                                 f'inline; filename="{row.id}.{pod_files.EXT[row.content_type]}"',
                             "X-Content-Type-Options": "nosniff"})
