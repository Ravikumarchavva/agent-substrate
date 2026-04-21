"""Element endpoints – upload and serve binary attachments.

POST /threads/{thread_id}/elements – upload a file (multipart)
GET  /elements/{element_id}/content – stream binary content back
GET  /threads/{thread_id}/elements – list elements for a thread
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ravi.configs.settings import settings
from ravi.core.storage.base import FileStore
from ravi.core.storage.document import Document, store_document
from ravi.core.storage.tenant import FileScope, TenantContext
from ravi.server.context import ServerContext, get_ctx
from ravi.server.database import get_db
from ravi.server.models import Element
from ravi.server.schemas import ElementOut
from ravi.server.services import get_thread

logger = logging.getLogger(__name__)

router = APIRouter(tags=["elements"])


def _require_file_store(ctx: ServerContext) -> FileStore:
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store is not configured")
    return ctx.file_store


def _to_element_out(element: Element) -> ElementOut:
    props = element.props or {}
    size: int | None = None
    if element.size is not None:
        try:
            size = int(element.size)
        except ValueError:
            size = None
    return ElementOut(
        id=element.id,
        thread_id=element.thread_id,
        type=element.type,
        name=element.name,
        mime=element.mime,
        size=size,
        display=element.display,
        url=element.url,
        for_id=element.for_id,
        props=props,
        document_type=props.get("document_type"),
        document_class=props.get("document_class"),
    )


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
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file attachment and store it via the configured FileStore."""
    thread = await get_thread(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    content = await file.read()
    max_bytes = settings.FILE_MAX_UPLOAD_BYTES
    if len(content) > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {max_mb:.0f} MB limit",
        )

    file_store = _require_file_store(ctx)
    document = await store_document(
        file_store,
        tenant=TenantContext(thread_id=str(thread_id)),
        name=file.filename or "untitled",
        content=content,
        content_type=file.content_type,
        scope=FileScope.UPLOADS,
        metadata={"source": "elements", "display": display},
    )

    element = Element(
        thread_id=thread_id,
        name=document.name,
        type=_element_type_for(document),
        mime=document.content_type,
        size=str(document.size_bytes),
        display=display,
        for_id=for_id,
        object_key=document.object_key,
        props=document.descriptor(),
    )
    db.add(element)
    await db.flush()
    element.url = f"/elements/{element.id}/content"
    await db.refresh(element)
    return _to_element_out(element)


@router.get("/elements/{element_id}/content")
async def get_element_content(
    element_id: uuid.UUID,
    ctx: ServerContext = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
):
    """Stream binary content of an element."""
    result = await db.execute(select(Element).where(Element.id == element_id))
    element = result.scalar_one_or_none()
    if element is None:
        raise HTTPException(status_code=404, detail="Element not found")

    content: bytes | None = None
    if element.object_key and ctx.file_store is not None:
        try:
            content = await ctx.file_store.get(element.object_key)
        except FileNotFoundError:
            logger.warning(
                "Missing object for element %s at key %s",
                element.id,
                element.object_key,
            )
    if content is None and element.content is not None:
        content = element.content
    if content is None:
        if element.object_key and ctx.file_store is None:
            raise HTTPException(status_code=503, detail="File store is not configured")
        raise HTTPException(status_code=404, detail="Element has no stored content")

    disposition = "inline" if element.display == "inline" else "attachment"

    return Response(
        content=content,
        media_type=element.mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{element.name}"',
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
    result = await db.execute(select(Element).where(Element.thread_id == thread_id))
    return [_to_element_out(element) for element in result.scalars().all()]


def _element_type_for(document: Document) -> str:
    """Map a document subtype into the existing element type vocabulary."""
    if document.document_type in {"image", "audio", "video", "pdf"}:
        return document.document_type
    return "file"
