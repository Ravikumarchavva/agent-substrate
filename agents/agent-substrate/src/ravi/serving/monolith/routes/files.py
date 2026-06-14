"""File upload / download / presign / delete endpoints.

Routes:
  POST   /files/upload           – upload a file; returns FileUploadResponse
  GET    /files/{file_id}/download – stream raw bytes back
  GET    /files/{file_id}/url      – presigned (or download) URL
  DELETE /files/{file_id}          – soft-delete metadata + delete from store
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ravi.serving.monolith.database import get_db
from ravi.serving.monolith.dependencies import ServerDependencies, get_ctx
from ravi.serving.monolith.models import FileMetadata
from ravi.serving.monolith.security.deps import get_current_user
from ravi.serving.shared.auth.claims import AuthClaims
from ravi.serving.shared.contracts.file_store import FileUploadResponse, FileUrlResponse

router = APIRouter(
    prefix="/files",
    tags=["files"],
    dependencies=[Depends(get_current_user)],
)

_MAX_BYTES = 200 * 1024 * 1024  # 200 MB hard ceiling (mirrors config default)


async def _get_meta(
    file_id: uuid.UUID,
    db: AsyncSession,
) -> FileMetadata:
    row = (
        await db.execute(
            select(FileMetadata).where(
                FileMetadata.id == file_id,
                FileMetadata.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    return row


@router.post("/upload", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    thread_id: Optional[uuid.UUID] = Form(None),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUploadResponse:
    """Upload a file and store its metadata."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {_MAX_BYTES // (1024 * 1024)} MB",
        )

    content_type = file.content_type or "application/octet-stream"
    original_name = file.filename or "upload"
    object_key = f"uploads/{uuid.uuid4()}/{original_name}"
    checksum = hashlib.sha256(data).hexdigest()

    await ctx.file_store.upload(object_key, data, content_type=content_type)

    meta = FileMetadata(
        object_key=object_key,
        original_name=original_name,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        org_id=claims.tenant_id,
        thread_id=thread_id,
        scope="uploads",
    )
    db.add(meta)
    await db.commit()
    await db.refresh(meta)

    return FileUploadResponse(
        id=meta.id,
        thread_id=meta.thread_id,
        name=meta.original_name,
        mime=meta.content_type,
        size=meta.size_bytes,
    )


@router.get("/{file_id}/download")
async def download_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Download file bytes."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta = await _get_meta(file_id, db)
    data = await ctx.file_store.download(meta.object_key)

    async def _stream():
        yield data

    return StreamingResponse(
        _stream(),
        media_type=meta.content_type,
        headers={"Content-Disposition": f'attachment; filename="{meta.original_name}"'},
    )


@router.get("/{file_id}/url", response_model=FileUrlResponse)
async def get_file_url(
    file_id: uuid.UUID,
    expires_in: int = 3600,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUrlResponse:
    """Return a presigned URL (or download URL for InMemoryFileStore)."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta = await _get_meta(file_id, db)
    url = await ctx.file_store.presign_url(meta.object_key, expires_in=expires_in)

    if url.startswith("memory://"):
        url = f"/files/{file_id}/download"

    return FileUrlResponse(url=url, expires_in=expires_in)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> None:
    """Soft-delete metadata and remove object from store."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta = await _get_meta(file_id, db)
    meta.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    await ctx.file_store.delete(meta.object_key)
