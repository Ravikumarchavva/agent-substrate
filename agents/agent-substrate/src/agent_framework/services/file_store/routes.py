"""File Store Service — HTTP routes.

Routes:
  POST   /artifacts/{thread_id}/files      — upload file
  GET    /artifacts/{thread_id}/files      — list files for thread
  GET    /artifacts/files/{file_id}        — get file metadata
  GET    /artifacts/files/{file_id}/download — download file
  DELETE /artifacts/files/{file_id}        — delete file
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.shared.database.dependency import get_db_session

from agent_framework.services.file_store.service import (
    create_file_record,
    delete_file,
    get_file,
    list_files,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


class FileOut(BaseModel):
    id: str
    thread_id: str
    original_name: str
    content_type: Optional[str]
    size_bytes: int
    storage_backend: str
    metadata: Optional[Dict[str, Any]]
    created_at: str


def _to_out(f) -> FileOut:
    return FileOut(
        id=str(f.id),
        thread_id=str(f.thread_id),
        original_name=f.original_name,
        content_type=f.content_type,
        size_bytes=f.size_bytes,
        storage_backend=f.storage_backend,
        metadata=f.metadata_,
        created_at=f.created_at.isoformat(),
    )


@router.post("/{thread_id}/files", status_code=201)
async def upload_file(
    thread_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Upload a file and associate it with a thread."""
    file_store = request.app.state.file_store
    content = await file.read()

    # Store file
    storage_key = f"{thread_id}/{uuid.uuid4()}/{file.filename}"
    await file_store.put(
        storage_key,
        content,
        content_type=file.content_type or "application/octet-stream",
    )

    # Create metadata record
    record = await create_file_record(
        db,
        thread_id=thread_id,
        original_name=file.filename or "unknown",
        storage_key=storage_key,
        content_type=file.content_type,
        size_bytes=len(content),
    )

    return _to_out(record)


@router.get("/{thread_id}/files")
async def list_thread_files(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
):
    files = await list_files(db, thread_id)
    return [_to_out(f) for f in files]


@router.get("/files/{file_id}")
async def get_file_metadata(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
):
    f = await get_file(db, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return _to_out(f)


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Download a file by ID."""
    from fastapi.responses import StreamingResponse
    import io

    f = await get_file(db, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")

    file_store = request.app.state.file_store
    content = await file_store.get(f.storage_key)

    return StreamingResponse(
        content=io.BytesIO(content),
        media_type=f.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{f.original_name}"'},
    )


@router.delete("/files/{file_id}", status_code=204)
async def delete_file_endpoint(
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    f = await get_file(db, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete from storage
    file_store = request.app.state.file_store
    try:
        await file_store.delete(f.storage_key)
    except Exception:
        logger.warning("Failed to delete file from storage: %s", f.storage_key)

    # Delete metadata
    await delete_file(db, file_id)
