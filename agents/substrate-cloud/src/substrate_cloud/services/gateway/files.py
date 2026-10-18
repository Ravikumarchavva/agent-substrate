"""Gateway: the file routes of the Agent API, over the caller's own file store.

The gateway keeps no files service: a file is its bytes in the caller's file store (Substrate Data's Files domain when the cell
has one, else the store's folder) plus the small record the monolith writes beside it (``file_records.py``). Both modes read and
write the same records, so a file uploaded through one is found through the other.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from substrate.stores import WorkspaceQuotaExceededError
from substrate.types.errors import ObjectNotFoundError
from substrate.workspace.layout import conversation_shared_key, user_upload_key

from substrate_cloud.file_records import FileIndex, FileRecord
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.contracts.file_store import (
    FileUploadResponse,
    FileUrlResponse,
)
from substrate_cloud.shared.problem import Problem
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else ""


def _safe_filename(name: str) -> str:
    """Basename only: no path separators from a client-supplied name."""
    return name.replace("\\", "/").rsplit("/", 1)[-1] or "upload"


def _is_previewable(content_type: str) -> bool:
    return (
        content_type.startswith(("image/", "text/"))
        or content_type == "application/pdf"
    )


def _store(request: Request, user: AuthClaims) -> Any:
    if not user.tenant_id:
        raise Problem(403, "forbidden", "Files belong to a user of an organization")
    return request.app.state.file_access.for_user(user.tenant_id, user.sub)


async def _get_record(
    request: Request, user: AuthClaims, file_id: uuid.UUID
) -> tuple[Any, FileIndex, FileRecord]:
    store = _store(request, user)
    index = FileIndex(store, user.tenant_id, user.sub)
    record = await index.live(file_id)
    # 404 whether the file is missing or someone else's: records sit in the caller's own folder, so another user's id is not there.
    if record is None:
        raise Problem(404, "not-found", "File not found")
    return store, index, record


async def _unique_key(store: Any, base_key: str) -> str:
    key, n = base_key, 0
    while await store.exists(key):
        n += 1
        stem, dot, ext = base_key.rpartition(".")
        key = f"{stem}-{n}{dot}{ext}" if dot else f"{base_key}-{n}"
    return key


@router.post("/upload", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    thread_id: Optional[uuid.UUID] = Form(None),
    user: AuthClaims = Depends(get_current_user),
) -> FileUploadResponse:
    store = _store(request, user)
    if thread_id is not None:
        # Refused before the body is read; 404 for a conversation that is not the caller's.
        await request.app.state.conversation_client.request(
            "GET", f"/threads/{thread_id}", _token(request)
        )
    limit = settings.FILE_MAX_UPLOAD_BYTES
    data = await file.read()
    if len(data) > limit:
        raise Problem(
            413,
            "payload-too-large",
            f"File exceeds the maximum size of {limit // (1024 * 1024)} MB",
        )

    name = _safe_filename(file.filename or "upload")
    content_type = file.content_type or "application/octet-stream"
    base_key = (
        conversation_shared_key(
            user.tenant_id, user.sub, str(thread_id), f"uploads/{name}"
        )
        if thread_id is not None
        else user_upload_key(user.tenant_id, user.sub, name)
    )
    key = await _unique_key(store, base_key)
    try:
        await store.upload(key, data, content_type=content_type)
    except WorkspaceQuotaExceededError as exc:
        raise Problem(413, "payload-too-large", str(exc)) from exc

    record = FileRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        owner=user.sub,
        object_key=key,
        original_name=name,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        thread_id=thread_id,
        uploaded_at=datetime.now(timezone.utc),
    )
    try:
        await FileIndex(store, user.tenant_id, user.sub).save(record)
    except WorkspaceQuotaExceededError as exc:
        await store.delete(
            key
        )  # no room even for the record: the file does not stay behind unrecorded
        raise Problem(413, "payload-too-large", str(exc)) from exc
    return FileUploadResponse(
        id=record.id,
        thread_id=record.thread_id,
        name=record.original_name,
        mime=record.content_type,
        size=record.size_bytes,
    )


@router.get("/documents")
async def list_thread_documents(
    request: Request,
    thread_id: uuid.UUID = Query(...),
    user: AuthClaims = Depends(get_current_user),
) -> list[dict]:
    """The files attached to one conversation. (The monolith adds each document's model-written card; that library is not part of
    microservices mode yet, so ``state`` is always ``none``.)"""
    await request.app.state.conversation_client.request(
        "GET", f"/threads/{thread_id}", _token(request)
    )
    index = FileIndex(_store(request, user), user.tenant_id, user.sub)
    return [
        {
            "file_id": str(r.id),
            "name": r.original_name,
            "state": "none",
            "description": None,
            "topics": [],
            "pages": r.page_count,
        }
        for r in await index.for_thread(thread_id)
    ]


@router.get("/{file_id}/status")
async def get_file_status(
    file_id: uuid.UUID, request: Request, user: AuthClaims = Depends(get_current_user)
) -> dict:
    _, _, record = await _get_record(request, user, file_id)
    return {
        "staged_at": record.staged_at,
        "staging_error": record.staging_error,
        "page_count": record.page_count,
        "created_at": record.created_at,
    }


@router.get("/{file_id}/download")
async def download_file(
    file_id: uuid.UUID, request: Request, user: AuthClaims = Depends(get_current_user)
) -> StreamingResponse:
    store, _, record = await _get_record(request, user, file_id)
    try:
        data = await store.download(record.object_key)
    except ObjectNotFoundError as exc:
        raise Problem(404, "not-found", "File not found") from exc

    async def body():
        yield data

    disposition = "inline" if _is_previewable(record.content_type) else "attachment"
    return StreamingResponse(
        body(),
        media_type=record.content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{record.original_name}"'
        },
    )


@router.get("/{file_id}/url", response_model=FileUrlResponse)
async def get_file_url(
    file_id: uuid.UUID,
    request: Request,
    expires_in: int = 3600,
    user: AuthClaims = Depends(get_current_user),
) -> FileUrlResponse:
    store, _, record = await _get_record(request, user, file_id)
    url = await store.presign_url(record.object_key, expires_in=expires_in)
    if url.startswith("memory://"):  # a store with no direct URL (the local folder)
        url = f"/api/v1/files/{file_id}/download"
    return FileUrlResponse(url=url, expires_in=expires_in)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID, request: Request, user: AuthClaims = Depends(get_current_user)
) -> None:
    store, index, record = await _get_record(request, user, file_id)
    await store.delete(record.object_key)
    await store.delete(f"{record.object_key}.extracted.md")
    await index.remove(record)
