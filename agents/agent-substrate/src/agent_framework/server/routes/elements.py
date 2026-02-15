"""Element endpoints – upload and serve binary attachments.

POST /threads/{thread_id}/elements – upload a file (multipart)
GET  /elements/{element_id}/content – stream binary content back
GET  /threads/{thread_id}/elements – list elements for a thread
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.server.database import get_db
from agent_framework.server.models import Element
from agent_framework.server.schemas import ElementOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["elements"])


@router.post(
    "/threads/{thread_id}/elements",
    response_model=ElementOut,
    status_code=201,
)
async def upload_element(
    thread_id: uuid.UUID,
    file: UploadFile = File(...),
    display: str = Form("inline"),
    for_id: uuid.UUID | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment and store it in the database."""
    content = await file.read()

    element = Element(
        thread_id=thread_id,
        name=file.filename or "untitled",
        type=_classify_mime(file.content_type),
        mime=file.content_type,
        size=str(len(content)),
        display=display,
        for_id=for_id,
        content=content,
    )
    db.add(element)
    await db.flush()
    await db.refresh(element)
    return element


@router.get("/elements/{element_id}/content")
async def get_element_content(
    element_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Stream binary content of an element."""
    result = await db.execute(
        select(Element).where(Element.id == element_id)
    )
    element = result.scalar_one_or_none()
    if element is None:
        raise HTTPException(status_code=404, detail="Element not found")
    if element.content is None:
        raise HTTPException(status_code=404, detail="Element has no stored content")

    return Response(
        content=element.content,
        media_type=element.mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{element.name}"',
        },
    )


@router.get(
    "/threads/{thread_id}/elements",
    response_model=list[ElementOut],
)
async def list_elements(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """List all elements for a thread."""
    result = await db.execute(
        select(Element).where(Element.thread_id == thread_id)
    )
    return result.scalars().all()


def _classify_mime(content_type: str | None) -> str:
    """Map MIME type to a simple element type string."""
    if not content_type:
        return "file"
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("audio/"):
        return "audio"
    if content_type.startswith("video/"):
        return "video"
    if content_type == "application/pdf":
        return "pdf"
    return "file"
