"""File upload / list / delete / download endpoints.

Routes:
  POST   /threads/{thread_id}/files                    – upload a file
  GET    /threads/{thread_id}/files                    – list files for thread
  DELETE /threads/{thread_id}/files/{file_id}          – delete a file
  GET    /threads/{thread_id}/files/{file_id}/content  – download raw content
  GET    /threads/{thread_id}/files/{file_id}/url      – pre-signed download URL
"""

from __future__ import annotations

import mimetypes
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.configs.settings import settings
from agent_framework.server.context import ServerContext, get_ctx
from agent_framework.server.database import get_db
from agent_framework.server.schemas import FileOut
from agent_framework.server.services import get_thread
from agent_framework.server.services.file_service import (
    delete_file,
    get_file,
    get_file_content,
    get_file_url,
    list_files,
    save_file,
)

router = APIRouter(prefix="/threads", tags=["files"])


@router.post("/{thread_id}/files", response_model=FileOut, status_code=201)
async def upload_file(
    thread_id: uuid.UUID,
    file: UploadFile = File(...),
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file and attach it to a thread.

    Accepts any file type up to the configured max upload size.
    The file is stored in the external FileStore and its metadata is
    recorded in the database. The returned ID can be included in
    subsequent chat requests.
    """
    thread = await get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    max_bytes = settings.FILE_MAX_UPLOAD_BYTES
    raw = await file.read()
    if len(raw) > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {max_mb:.0f} MB limit",
        )

    # Infer MIME type when the browser sends a generic one
    mime = file.content_type or ""
    if not mime or mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file.filename or "")
        mime = guessed or "application/octet-stream"

    meta = await save_file(
        db,
        ctx.file_store,
        thread_id=thread_id,
        name=file.filename or "upload",
        mime=mime,
        content=raw,
    )
    await db.commit()

    return FileOut(
        id=meta.id,
        thread_id=meta.thread_id,
        name=meta.original_name,
        mime=meta.content_type,
        size=meta.size_bytes,
    )


@router.get("/{thread_id}/files", response_model=List[FileOut])
async def list_thread_files(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all files attached to a thread."""
    thread = await get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    files = await list_files(db, thread_id)
    return [
        FileOut(
            id=f.id,
            thread_id=f.thread_id,
            name=f.original_name,
            mime=f.content_type,
            size=f.size_bytes,
        )
        for f in files
    ]


@router.delete("/{thread_id}/files/{file_id}", status_code=204)
async def delete_thread_file(
    thread_id: uuid.UUID,
    file_id: uuid.UUID,
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Delete a file from a thread."""
    found = await delete_file(db, ctx.file_store, file_id, thread_id)
    if not found:
        raise HTTPException(status_code=404, detail="File not found")
    await db.commit()


@router.get("/{thread_id}/files/{file_id}/content")
async def download_file(
    thread_id: uuid.UUID,
    file_id: uuid.UUID,
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Download the raw file content from the FileStore."""
    meta = await get_file(db, file_id, thread_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    raw = await get_file_content(ctx.file_store, meta)
    return Response(
        content=raw,
        media_type=meta.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{meta.original_name}"',
        },
    )


@router.get("/{thread_id}/files/{file_id}/url")
async def get_download_url(
    thread_id: uuid.UUID,
    file_id: uuid.UUID,
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed download URL for a file.

    For S3 backends, returns a time-limited HTTPS URL.
    For local backends, returns a file:// URI.
    """
    meta = await get_file(db, file_id, thread_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        url = await get_file_url(ctx.file_store, meta)
        return {"url": url, "expires_in": 3600}
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Direct download URLs are not available with encrypted storage. "
                   "Use the /content endpoint instead.",
        )
