"""File upload / list / delete / download endpoints.

Routes:
  POST   /threads/{thread_id}/files                    – upload a file
  GET    /threads/{thread_id}/files                    – list files for thread
  DELETE /threads/{thread_id}/files/{file_id}          – delete a file
  GET    /threads/{thread_id}/files/{file_id}/content  – download raw content
"""

from __future__ import annotations

import mimetypes
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.server.database import get_db
from agent_framework.server.schemas import FileOut
from agent_framework.server.services import get_thread
from agent_framework.server.services.file_service import (
    delete_file,
    get_file,
    list_files,
    save_file,
)

router = APIRouter(prefix="/threads", tags=["files"])

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/{thread_id}/files", response_model=FileOut, status_code=201)
async def upload_file(
    thread_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file and attach it to a thread.

    Accepts any file type up to 50 MB.
    The file is stored in the database and its ID is returned so the
    frontend can include it in subsequent chat requests.
    """
    thread = await get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    # Infer MIME type when the browser sends a generic one
    mime = file.content_type or ""
    if not mime or mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(file.filename or "")
        mime = guessed or "application/octet-stream"

    element = await save_file(
        db,
        thread_id=thread_id,
        name=file.filename or "upload",
        mime=mime,
        content=raw,
    )
    await db.commit()

    return FileOut(
        id=element.id,
        thread_id=element.thread_id,
        name=element.name,
        mime=element.mime,
        size=str(len(raw)),
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

    elements = await list_files(db, thread_id)
    return [
        FileOut(
            id=el.id,
            thread_id=el.thread_id,
            name=el.name,
            mime=el.mime,
            size=el.size,
        )
        for el in elements
    ]


@router.delete("/{thread_id}/files/{file_id}", status_code=204)
async def delete_thread_file(
    thread_id: uuid.UUID,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a file from a thread."""
    found = await delete_file(db, file_id, thread_id)
    if not found:
        raise HTTPException(status_code=404, detail="File not found")
    await db.commit()


@router.get("/{thread_id}/files/{file_id}/content")
async def download_file(
    thread_id: uuid.UUID,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download the raw file content."""
    element = await get_file(db, file_id, thread_id)
    if not element or not element.content:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(
        content=element.content,
        media_type=element.mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{element.name}"'},
    )
