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

from substrate.capabilities.storage.workspace import WorkspaceQuotaExceededError
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import FileMetadata, User
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims
from substrate.serving.shared.contracts.file_store import (
    FileUploadResponse,
    FileUrlResponse,
)

router = APIRouter(
    prefix="/files",
    tags=["files"],
    dependencies=[Depends(get_current_user)],
)

_MAX_BYTES = 200 * 1024 * 1024  # 200 MB hard ceiling (mirrors config default)


def _safe_filename(name: str) -> str:
    """Basename only — strip any path separators from a client-supplied name."""
    return name.replace("\\", "/").rsplit("/", 1)[-1] or "upload"


def _is_previewable(content_type: str) -> bool:
    """Types browsers can render inline — everything else forces a download."""
    return (
        content_type.startswith("image/")
        or content_type.startswith("text/")
        or content_type == "application/pdf"
    )


async def _unique_object_key(db: AsyncSession, base_key: str) -> str:
    """Append -1, -2, ... on collision so uploads never clobber each other."""
    key = base_key
    suffix = 0
    while (
        await db.execute(select(FileMetadata.id).where(FileMetadata.object_key == key))
    ).scalar_one_or_none() is not None:
        suffix += 1
        stem, dot, ext = base_key.rpartition(".")
        key = f"{stem}-{suffix}{dot}{ext}" if dot else f"{base_key}-{suffix}"
    return key


async def _ensure_user(db: AsyncSession, user_id: uuid.UUID, email: str) -> None:
    """Get-or-create the ``users`` row backing *user_id*.

    ``FileMetadata.user_id`` is a real FK to ``users.id`` — a caller whose
    JWT ``sub`` is a valid UUID but has never been seen before (e.g. a
    frontend that mints per-user tokens straight from its own user store,
    like substrate-ui's Google-OAuth Prisma user id) would otherwise hit an
    IntegrityError on insert. Idempotent; safe to call on every upload.
    """
    existing = (
        await db.execute(select(User.id).where(User.id == user_id))
    ).scalar_one_or_none()
    if existing is not None:
        return
    db.add(User(id=user_id, identifier=email or str(user_id)))
    try:
        await db.commit()
    except Exception:
        # Lost a create race against a concurrent request for the same
        # user — the row exists now either way, just roll back this
        # attempt's failed insert.
        await db.rollback()


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
    """Upload a file and store its metadata.

    Object keys are scoped by user (and thread, when given): ``users/{sub}/
    sessions/{thread_id}/{name}`` or ``users/{sub}/uploads/{name}``. This is
    the same key layout the code interpreter's per-user PVC subPath mount
    exposes, so a thread-scoped upload lands exactly where that thread's
    sandbox session can see it.
    """
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {_MAX_BYTES // (1024 * 1024)} MB",
        )

    content_type = file.content_type or "application/octet-stream"
    original_name = _safe_filename(file.filename or "upload")
    checksum = hashlib.sha256(data).hexdigest()

    if thread_id is not None:
        base_key = f"users/{claims.sub}/sessions/{thread_id}/{original_name}"
    else:
        base_key = f"users/{claims.sub}/uploads/{original_name}"
    object_key = await _unique_object_key(db, base_key)

    try:
        await ctx.file_store.upload(object_key, data, content_type=content_type)
    except WorkspaceQuotaExceededError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    try:
        user_uuid: Optional[uuid.UUID] = uuid.UUID(claims.sub)
    except ValueError:
        user_uuid = None
    else:
        await _ensure_user(db, user_uuid, claims.email)

    meta = FileMetadata(
        object_key=object_key,
        original_name=original_name,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        org_id=claims.tenant_id,
        user_id=user_uuid,
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

    disposition = "inline" if _is_previewable(meta.content_type) else "attachment"
    return StreamingResponse(
        _stream(),
        media_type=meta.content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{meta.original_name}"'
        },
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
