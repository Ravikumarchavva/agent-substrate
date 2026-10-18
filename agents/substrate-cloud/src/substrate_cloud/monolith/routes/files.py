"""File upload / download / presign / delete endpoints.

Routes:
  POST   /files/upload                    – upload a file through this server; returns FileUploadResponse
  POST   /files/uploads                   – start a direct upload: bytes go from the browser to object storage on signed URLs
  POST   /files/uploads/{id}/complete     – finish it
  GET    /files/{file_id}/download        – stream raw bytes back
  GET    /files/{file_id}/url             – presigned (or download) URL
  DELETE /files/{file_id}                 – delete the file and its record

A file is its bytes in the caller's file store plus a small record beside them (``file_records.py``); there is no files table.
Every lookup is under the *caller's* folder, so someone else's file id is a 404 whether or not it exists.
"""

from __future__ import annotations

import logging

import asyncio
import hashlib
import io
import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.workspace.layout import conversation_shared_key, user_upload_key
from substrate.stores import WorkspaceQuotaExceededError
from substrate.types.errors import (
    KernelError,
    ObjectNotFoundError,
    ServiceUnavailableError,
)
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.file_records import FileIndex, FileRecord, key_owner
from substrate_cloud.monolith.models import Thread
from substrate_cloud.monolith.routes.chat_context import (
    EXTRACTABLE_CONTENT_TYPES,
    _session_relative_path,
)
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.monolith.services import get_owned_thread
from substrate_cloud.monolith.services.agents.service import get_owned_agent
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.contracts.file_store import (
    DirectUploadComplete,
    DirectUploadRequest,
    DirectUploadTicket,
    FileUploadResponse,
    FileUrlResponse,
)
from substrate_cloud.shared.doc_quota import (
    check_and_increment,
    peek,
    seconds_until_reset,
)
from substrate_cloud.shared.settings import settings
from substrate.documents import ExtractionResult
from substrate_cloud.document_reader import document_reader

logger = logging.getLogger(__name__)

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


def _index(ctx: ServerDependencies, claims: AuthClaims) -> FileIndex:
    """The caller's own file records. Records live in the caller's folder, so this can only ever see the caller's files."""
    store = ctx.files_for(claims.tenant_id, claims.sub)
    if store is None:
        raise HTTPException(status_code=503, detail="File store not configured")
    return FileIndex(store, claims.tenant_id, claims.sub)


async def _unique_object_key(store: Any, base_key: str) -> str:
    """Append -1, -2, ... on collision so uploads never clobber each other."""
    key = base_key
    suffix = 0
    while await store.exists(key):
        suffix += 1
        stem, dot, ext = base_key.rpartition(".")
        key = f"{stem}-{suffix}{dot}{ext}" if dot else f"{base_key}-{suffix}"
    return key


async def _get_meta(
    file_id: uuid.UUID,
    ctx: ServerDependencies,
    claims: AuthClaims,
) -> FileRecord:
    record = await _index(ctx, claims).live(file_id)
    # 404 whether the file is missing or just not this caller's — never a distinct 403, so this endpoint isn't an existence oracle
    # for file ids that happen to leak into logs/URLs (same rationale as thread_service.get_owned_thread). Records sit in the
    # caller's own folder, so another user's id is simply not there to be found.
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    return record


async def _may_access_key(key: str, claims: AuthClaims, db: AsyncSession) -> bool:
    """Ownership check for an object key — uploads, RAG-extracted images and other capability-written artifacts alike.
    Structural, not a raw prefix trust: every key starts ``tenants/{tenant_id}/...``, where ``tenant_id`` came from the server's
    own JWT-minted keys, never from this request — so checking it is checking real ownership, not an attacker-controlled string.
    """
    if claims.is_admin:
        return True
    parts = key.split("/")
    if len(parts) < 2 or parts[0] != "tenants" or parts[1] != claims.tenant_id:
        return False
    if len(parts) >= 4 and parts[2] == "users" and parts[3] == claims.sub:
        return True  # the caller's own direct-upload prefix
    if len(parts) >= 3 and parts[2] == "knowledge":
        # Knowledge-base documents are project-shared: any same-tenant
        # caller may read them (replaces the old blanket, cross-tenant
        # `_OPEN_NAMESPACES = ("kb/",)` rule with a tenant-scoped one).
        return True
    if len(parts) >= 6 and parts[2] == "users" and parts[4] == "conversations":
        try:
            thread_uuid = uuid.UUID(parts[5])
        except ValueError:
            return False
        owned = (
            await db.execute(
                select(Thread.id).where(
                    Thread.id == thread_uuid,
                    Thread.user_identifier == claims.sub,
                    Thread.tenant_id == claims.tenant_id,
                )
            )
        ).scalar_one_or_none()
        return owned is not None
    return False


def _pdf_page_count(data: bytes) -> int:
    """Cheap page count via pypdf — no OCR/layout model involved. Raises
    ValueError on a corrupt/unreadable PDF (caller turns that into a 422,
    same as any other malformed upload)."""
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(data)).pages)


def render_page_marked_markdown(result: ExtractionResult) -> Optional[str]:
    """Render an already-extracted ``ExtractionResult`` into page-marked
    plain text for ``code_interpreter`` to read instead of re-parsing a
    PDF's raw bytes. Keeps page boundaries — and any image/table captions
    the extraction returned — instead of joining everything into one blob.
    Returns ``None`` when nothing could be rendered.

    Page numbers are metadata, not content: an HTML comment (invisible when
    rendered, and not real document structure), never a Markdown heading. A
    ``## Page N`` heading previously sat indistinguishable from a genuine
    section heading in the document itself — a model asked to "convert this
    to Word" read it as literal structure and reproduced "Page 1" / "Page 2"
    headings in its output that were never in the source.
    """
    if not result.pages:
        return None

    captions_by_page: dict[int, list[str]] = {}
    for page in result.pages:
        for image in page.images:
            if image.caption and image.page_number is not None:
                captions_by_page.setdefault(image.page_number, []).append(image.caption)

    sections: list[str] = []
    for page in result.pages:
        sections.append(f"<!-- page {page.page_number} -->\n{page.text.strip()}")
        for caption in captions_by_page.get(page.page_number, []):
            sections.append(f"> Image/table caption: {caption}")
    return "\n\n".join(sections).strip() or None


async def _write_extracted_sidecar(
    store: Any,
    file_id: uuid.UUID,
    result: ExtractionResult,
    *,
    object_key: str,
) -> None:
    """Write a ``{original_name}.extracted.md`` sidecar next to the uploaded file, so ``code_interpreter`` (which mounts that same
    directory) can read pipeline-quality extracted text instead of pypdf-ing the raw PDF bytes itself.
    Best-effort: never raises, never affects staging success/failure."""
    try:
        if store is None:
            return
        text = render_page_marked_markdown(result)
        if not text:
            return
        sidecar_key = f"{object_key}.extracted.md"
        await store.upload(
            sidecar_key, text.encode("utf-8"), content_type="text/markdown"
        )
    except Exception as exc:
        logger.warning("Writing extracted sidecar failed for file %s: %s", file_id, exc)


async def _stage_uploaded_doc(
    ctx: ServerDependencies,
    record: FileRecord,
    data: bytes,
) -> None:
    """Fire-and-forget: read the upload **once**, file it in the conversation's documents (``Library.add`` — an OKF bundle and a
    catalog, no embeddings), and write the ``.extracted.md`` sidecar from that same read. Same in-process ``asyncio.create_task``
    pattern as ``routes/scheduled.py``'s ``_run_bg`` — no durable job queue in this codebase. If the server restarts mid-task,
    ``staged_at`` simply never gets set; the next message that references the file files it then (``_build_file_context``), and
    meanwhile blocks with a clear "still processing" error."""
    assert ctx.library is not None
    from substrate.documents import DocumentError
    from substrate_cloud.documents_library import documents_collection

    store = ctx.files_for(record.tenant_id, record.owner)
    index = FileIndex(store, record.tenant_id, record.owner)
    try:
        collection = documents_collection(
            record.tenant_id, record.owner, str(record.thread_id)
        )
        if collection is None:
            raise DocumentError("this upload has no conversation to file it under")
        result = await document_reader().read(
            data, record.original_name, content_type=record.content_type
        )
        added = await ctx.library.add(
            result,
            record.original_name,
            collection=collection,
            resource=record.object_key,
            sha256=record.checksum_sha256,
            metadata={
                "file_id": str(record.id),
                "session_path": _session_relative_path(record.object_key)
                or record.original_name,
                "thread_id": str(record.thread_id),
            },
        )
    except Exception as exc:
        # Full detail goes to the server log only — staging_error is served straight to the UI as-is (the status endpoint), and
        # the raw exception text (a CUDA OOM traceback, a stack trace, an internal service's error body) is neither meaningful
        # nor safe to show a user: it leaks infra internals and reads as broken, not as something they can act on.
        logger.warning("Eager staging failed for file %s: %s", record.id, exc)
        current = await index.get(record.id)
        if current is not None:
            current.staging_error = (
                "Couldn't process this document automatically. "
                "It may be too large or complex — try a smaller file, "
                "or ask the assistant about it directly."
            )
            await index.save(current)
        return
    # The document is filed and readable. Describing it (what each section says, a card) is separate, later and optional: it never
    # delays ``staged_at`` or the first message, and a model that is down just leaves the counted index.
    if ctx.enrichment is not None:
        ctx.enrichment.submit(ctx.library, collection, added.document)
    await _write_extracted_sidecar(
        store,
        record.id,
        result,
        object_key=record.object_key,
    )
    current = await index.get(record.id)
    if current is not None:
        current.staged_at = datetime.now(timezone.utc)
        await index.save(current)


def _validate_extractable(
    content_type: str, size: int, page_count: Optional[int]
) -> None:
    max_doc_bytes = settings.RAG_MAX_DOC_MB * 1024 * 1024
    if size > max_doc_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Document exceeds maximum size of {settings.RAG_MAX_DOC_MB} MB",
        )
    if page_count is not None and page_count > settings.RAG_MAX_DOC_PAGES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Document has {page_count} pages, exceeding the "
                f"{settings.RAG_MAX_DOC_PAGES}-page limit."
            ),
        )


async def _check_upload_attempts(request: Request, claims: AuthClaims) -> None:
    """The upload-attempt quota bounds eager-staging compute abuse (repeated upload-then-discard)."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return
    allowed, _remaining = await check_and_increment(
        redis,
        "docquota:upload",
        claims.sub,
        settings.RAG_DAILY_UPLOAD_ATTEMPT_LIMIT,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily upload limit ({settings.RAG_DAILY_UPLOAD_ATTEMPT_LIMIT}) "
                "reached — try again tomorrow."
            ),
        )


async def _owned_thread(
    db: AsyncSession, claims: AuthClaims, thread_id: Optional[uuid.UUID]
) -> Optional[Thread]:
    """The conversation an upload is for, which the caller must own (``None`` for a thread-less upload)."""
    thread = (
        await get_owned_thread(db, thread_id, claims) if thread_id is not None else None
    )
    if thread_id is not None and thread is None:
        # Without this, any authenticated caller could tag an upload with someone else's thread_id: the bytes land in the caller's
        # own folder (harmless there), but the record would carry that thread_id — a confused-deputy path into a conversation the
        # attacker doesn't own if anything trusts the record's thread for context/attachment lookups without re-checking ownership.
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


async def _object_key_for(
    db: AsyncSession,
    claims: AuthClaims,
    store: Any,
    *,
    name: str,
    thread: Optional[Thread],
    thread_id: Optional[uuid.UUID],
) -> str:
    """The object key a new upload goes to.

    Object keys are scoped by tenant, user, and thread (when given):
    ``tenants/{tid}/users/{uid}/conversations/{thread_id}/branches/main/workspace/shared/uploads/{name}`` or, with no thread,
    ``tenants/{tid}/users/{uid}/uploads/{name}`` — see ``workspace/layout.py``. In a conversation with an agent this is the agent's
    own home, which its sandbox opens as ``/workspace``, so the upload is there for its code."""
    if thread_id is not None:
        # A conversation with an agent keeps its files in the agent's own workspace, where the agent's sandbox looks for them.
        upload_agent = (
            await get_owned_agent(db, thread.agent_id, claims)
            if thread is not None and thread.agent_id
            else None
        )
        base_key = conversation_shared_key(
            claims.tenant_id,
            claims.sub,
            upload_agent.workspace_id if upload_agent else str(thread_id),
            f"uploads/{name}",
        )
    else:
        base_key = user_upload_key(claims.tenant_id, claims.sub, name)
    return await _unique_object_key(store, base_key)


async def _save_or_undo(index: FileIndex, store: Any, record: FileRecord) -> None:
    """Write the record; if the owner's quota has no room even for that, the file does not stay behind unrecorded."""
    try:
        await index.save(record)
    except WorkspaceQuotaExceededError as exc:
        await store.delete(record.object_key)
        raise HTTPException(status_code=413, detail=str(exc)) from exc


def _stage_later(ctx: ServerDependencies, record: FileRecord, data: bytes) -> None:
    if ctx.library is not None and record.thread_id is not None:
        asyncio.create_task(_stage_uploaded_doc(ctx, record, data))


def _upload_response(record: FileRecord) -> FileUploadResponse:
    return FileUploadResponse(
        id=record.id,
        thread_id=record.thread_id,
        name=record.original_name,
        mime=record.content_type,
        size=record.size_bytes,
        # object_key is fixed at upload time (see conversation_shared_key(...) above), so this can be computed immediately rather
        # than waiting for the first chat turn. Lets the composer's optimistic just-sent render open the file in the read-only
        # viewer right away, instead of only after a reload picks up chat_context.py's own (identically-derived) session_path on
        # the persisted message.
        session_path=(
            _session_relative_path(record.object_key)
            if record.thread_id is not None
            else None
        ),
    )


@router.post("/upload", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    thread_id: Optional[uuid.UUID] = Form(None),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUploadResponse:
    """Upload a file through this server (small files, scripts, tests). Browsers use ``POST /files/uploads`` and send the bytes
    themselves, straight to object storage.

    RAG-eligible types (currently PDF only — see ``EXTRACTABLE_CONTENT_TYPES``) get extra, synchronous-before-storing checks
    (upload-attempt quota, size cap, page cap) plus eager background staging (the document is read once and filed in the
    conversation's documents) once stored, when a ``thread_id`` is already known — see ``_stage_uploaded_doc``. Other file types
    are unaffected: pure blob + record storage."""
    index = _index(ctx, claims)
    store = ctx.files_for(claims.tenant_id, claims.sub)
    upload_thread = await _owned_thread(
        db, claims, thread_id
    )  # a foreign thread is refused before the body is read

    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {_MAX_BYTES // (1024 * 1024)} MB",
        )

    content_type = file.content_type or "application/octet-stream"
    original_name = _safe_filename(file.filename or "upload")
    checksum = hashlib.sha256(data).hexdigest()

    page_count: Optional[int] = None
    is_extractable = content_type in EXTRACTABLE_CONTENT_TYPES
    # No thread_id yet -> no conversation to file the document under. Deferred to send time instead, once the real thread_id is
    # known — see chat_context.py::_build_file_context.
    will_stage = is_extractable and ctx.library is not None and thread_id is not None
    if is_extractable:
        if will_stage:
            await _check_upload_attempts(request, claims)
        _validate_extractable(
            content_type, len(data), None
        )  # size first: counting pages of a huge file is the cost to avoid
        if content_type == "application/pdf":
            try:
                page_count = _pdf_page_count(data)
            except Exception as exc:
                raise HTTPException(
                    status_code=422, detail=f"Could not read PDF: {exc}"
                ) from exc
            _validate_extractable(content_type, len(data), page_count)

    object_key = await _object_key_for(
        db, claims, store, name=original_name, thread=upload_thread, thread_id=thread_id
    )
    try:
        await store.upload(object_key, data, content_type=content_type)
    except WorkspaceQuotaExceededError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    record = FileRecord(
        id=uuid.uuid4(),
        tenant_id=claims.tenant_id,
        owner=claims.sub,
        object_key=object_key,
        original_name=original_name,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        thread_id=thread_id,
        page_count=page_count,
        uploaded_at=datetime.now(timezone.utc),
    )
    await _save_or_undo(index, store, record)
    if will_stage:
        _stage_later(ctx, record, data)
    return _upload_response(record)


@router.post("/uploads", response_model=DirectUploadTicket, status_code=201)
async def start_direct_upload(
    body: DirectUploadRequest,
    request: Request,
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> DirectUploadTicket:
    """Start an upload whose bytes never pass through this server: the response carries signed URLs, the browser ``PUT``s the file
    (or each part) to them, then calls ``POST /files/uploads/{id}/complete``. The size is pinned into the signature, so the
    declared size is the enforced one."""
    direct = ctx.direct_files_for(claims.tenant_id, claims.sub)
    if direct is None:
        raise HTTPException(
            status_code=501,
            detail="Direct uploads need the data service (DATA_SERVICE_URL); use POST /files/upload.",
        )
    if body.size <= 0 or body.size > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {_MAX_BYTES // (1024 * 1024)} MB",
        )
    content_type = (
        body.content_type
        or mimetypes.guess_type(body.name)[0]
        or "application/octet-stream"
    )
    name = _safe_filename(body.name or "upload")
    upload_thread = await _owned_thread(db, claims, body.thread_id)
    if content_type in EXTRACTABLE_CONTENT_TYPES:
        _validate_extractable(content_type, body.size, None)
        if ctx.library is not None and body.thread_id is not None:
            await _check_upload_attempts(request, claims)
    object_key = await _object_key_for(
        db, claims, direct, name=name, thread=upload_thread, thread_id=body.thread_id
    )
    try:
        ticket = await direct.initiate_upload(
            object_key,
            body.size,
            content_type=content_type,
            sha256=body.sha256,
            if_none_match=True,
        )
    except WorkspaceQuotaExceededError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    record = FileRecord(
        id=uuid.uuid4(),
        tenant_id=claims.tenant_id,
        owner=claims.sub,
        object_key=object_key,
        original_name=name,
        content_type=content_type,
        size_bytes=body.size,
        checksum_sha256=body.sha256 or "",
        thread_id=body.thread_id,
        upload_id=ticket.upload_id,
    )
    await _index(ctx, claims).save(record)
    return DirectUploadTicket(
        id=record.id,
        method=ticket.method,
        url=ticket.url,
        headers=ticket.headers,
        parts=ticket.parts,
        part_size=ticket.part_size,
        expires_at=ticket.expires_at,
        session_path=(
            _session_relative_path(object_key) if body.thread_id is not None else None
        ),
    )


@router.post("/uploads/{file_id}/complete", response_model=FileUploadResponse)
async def complete_direct_upload(
    file_id: uuid.UUID,
    body: DirectUploadComplete,
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUploadResponse:
    """Finish a direct upload: the data service checks the stored size (and hash) and makes the new version atomically. Safe to
    retry. A document is then checked (page count) and, in a conversation, read and filed in the background like any upload."""
    direct = ctx.direct_files_for(claims.tenant_id, claims.sub)
    index = _index(ctx, claims)
    record = await index.get(file_id)
    if direct is None or record is None or record.upload_id is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        info = await direct.complete_upload(
            record.object_key,
            record.upload_id,
            [p.model_dump() for p in body.parts],
            if_none_match=True,
        )
    except WorkspaceQuotaExceededError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ServiceUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="File storage is unavailable; retry."
        ) from exc
    except KernelError as exc:
        raise HTTPException(
            status_code=409, detail="The upload did not complete; start it again."
        ) from exc
    record.size_bytes = info.size
    record.checksum_sha256 = info.sha256 or record.checksum_sha256
    data: bytes | None = None
    if record.content_type in EXTRACTABLE_CONTENT_TYPES:
        data = await direct.download(record.object_key)
        record.checksum_sha256 = (
            record.checksum_sha256 or hashlib.sha256(data).hexdigest()
        )
        if record.content_type == "application/pdf":
            try:
                record.page_count = _pdf_page_count(data)
                _validate_extractable(record.content_type, len(data), record.page_count)
            except HTTPException:
                await direct.delete(record.object_key)
                await index.remove(record)
                raise
            except Exception as exc:
                await direct.delete(record.object_key)
                await index.remove(record)
                raise HTTPException(
                    status_code=422, detail=f"Could not read PDF: {exc}"
                ) from exc
    record.upload_id = None
    record.uploaded_at = datetime.now(timezone.utc)
    await _save_or_undo(index, direct, record)
    if data is not None:
        _stage_later(ctx, record, data)
    return _upload_response(record)


@router.get("/quota/status")
async def get_doc_quota_status(
    request: Request,
    claims: AuthClaims = Depends(get_current_user),
) -> dict:
    """Read-only daily commit-quota usage for the sidebar's document-limit
    bar (mirrors routes/rate_limit.py's shape/pattern for the message-limit
    bar). Registered before /{file_id}/status so "quota" is never matched
    as a file_id path param — Starlette routes match in registration order,
    not most-specific-first."""
    redis = getattr(request.app.state, "redis", None)
    limit = settings.RAG_DAILY_DOC_LIMIT
    if redis is None:
        return {"enabled": False, "used": 0, "limit": limit, "reset_in": 0}
    used, _remaining = await peek(redis, "docquota:commit", claims.sub, limit)
    return {
        "enabled": True,
        "used": used,
        "limit": limit,
        "reset_in": seconds_until_reset(),
    }


@router.get("/object")
async def serve_object(
    key: str = Query(
        ...,
        description="Tenant-scoped object key owned by the caller",
    ),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Serve a stored object by key — the target of the ``/files/object?key=`` links in tool-result attachments (see
    ``agents/runtime/context/tool.py``) and of ``ask()``'s ``Citation.image_key``/``pdf_key`` (see ``integrations/knowledge/ask.py``).

    Deliberately a stable, authenticated app URL rather than a presigned one: the wire-event log is replayed months later, and a
    presigned link would have expired, leaving old conversations full of dead images.

    Authorization is structural, off the key's own tenant/user/conversation segments (``_may_access_key``): every key starts
    ``tenants/{tenant_id}/...`` and the server itself built it. In particular, knowledge-base keys are project-shared within a
    tenant, never a global open namespace across tenants. The read itself is made as the key's owner, so the data service applies
    its own ownership rule on top.

    Registered before ``/{file_id}/status`` so "object" is never matched as a file_id path param (Starlette matches in
    registration order).
    """
    if not await _may_access_key(key, claims, db):
        raise HTTPException(status_code=404, detail="Not found")
    tenant_id = key.split("/")[1] if claims.is_admin else claims.tenant_id
    store = ctx.files_for(tenant_id, key_owner(key) or claims.sub)
    if store is None:
        raise HTTPException(status_code=503, detail="File storage is not configured")
    try:
        data = await store.download(key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    mime = mimetypes.guess_type(key)[0] or "application/octet-stream"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={
            "Content-Length": str(len(data)),
            # Objects are immutable once written (a new version gets a new
            # key), so this can be cached hard.
            "Cache-Control": "private, max-age=31536000, immutable",
        },
    )


@router.get("/{file_id}/status")
async def get_file_status(
    file_id: uuid.UUID,
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    """Lightweight polling target for the composer's per-attachment progress ring — only reads the file's record. See
    ``substrate-ui``'s ``useFileAttachments.ts``: ``page_count``/``created_at`` drive a simulated (elapsed-time-based) progress
    estimate since true per-page extraction progress isn't available (PaddleOCR batches internally despite ``predict_iter()``
    looking lazy — verified, not assumed); ``staged_at``/``staging_error`` are the real ground truth for when the ring should snap
    to 100% or show an error state."""
    meta = await _get_meta(file_id, ctx, claims)
    return {
        "staged_at": meta.staged_at,
        "staging_error": meta.staging_error,
        "page_count": meta.page_count,
        "created_at": meta.created_at,
    }


def _document_of(ctx: Any, meta: FileRecord, claims: AuthClaims) -> tuple[str, str]:
    """The conversation collection and library document id of an uploaded file, or 404 when it was never filed."""
    from substrate_cloud.documents_library import documents_collection

    if ctx.library is None or meta.thread_id is None or not meta.checksum_sha256:
        raise HTTPException(status_code=404, detail="This file is not in the library")
    collection = documents_collection(claims.tenant_id, claims.sub, str(meta.thread_id))
    if collection is None:
        raise HTTPException(status_code=404, detail="This file is not in the library")
    return collection, ctx.library.document_id(meta.original_name, meta.checksum_sha256)


@router.get("/documents")
async def list_thread_documents(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> list[dict]:
    """The documents filed under one conversation, each with its model-written card and topics (``state`` is ``none`` until described)."""
    from substrate_cloud.documents_library import documents_collection

    if await get_owned_thread(db, thread_id, claims) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    collection = documents_collection(claims.tenant_id, claims.sub, str(thread_id))
    if ctx.library is None or collection is None:
        return []
    listing = await ctx.library.list(collection=collection)
    return [
        {
            "file_id": d.meta.get("file_id"),
            "name": d.filename,
            "state": d.enrichment,
            "description": d.description,
            "topics": list(d.topics),
            "pages": d.pages,
        }
        for d in listing.documents
    ]


@router.get("/{file_id}/document")
async def get_file_document(
    file_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict:
    """What the library knows about an uploaded file: the model-written card, where it is filed, and what each section says.
    ``state`` is ``none`` until it has been described, then ``running``, ``done`` or ``failed``. Everything model-written is
    machine-generated and unverified (``generated: true``)."""
    meta = await _get_meta(file_id, ctx, claims)
    collection, document = _document_of(ctx, meta, claims)
    outline = await ctx.library.outline(collection=collection, document=document)
    if outline is None:
        raise HTTPException(status_code=404, detail="This file is not in the library")
    info = outline.document
    return {
        "state": info.enrichment,
        "generated": info.description is not None,
        "description": info.description,
        "topics": list(info.topics),
        "pages": info.pages,
        "sections": [
            {
                "position": s.position,
                "title": s.title,
                "first_page": s.first_page,
                "last_page": s.last_page,
                "description": s.description,
            }
            for s in outline.sections
        ],
    }


@router.post("/{file_id}/document/describe", status_code=202)
async def describe_file_document(
    file_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict:
    """Have the document described again (a new card and section descriptions). Runs in the background; poll ``GET .../document``."""
    meta = await _get_meta(file_id, ctx, claims)
    collection, document = _document_of(ctx, meta, claims)
    if ctx.enrichment is None:
        raise HTTPException(status_code=503, detail="Summaries are turned off")
    ctx.enrichment.submit(ctx.library, collection, document, force=True)
    return {"state": "running"}


@router.get("/{file_id}/download")
async def download_file(
    file_id: uuid.UUID,
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Download file bytes."""
    meta = await _get_meta(file_id, ctx, claims)
    store = ctx.files_for(claims.tenant_id, claims.sub)
    try:
        data = await store.download(meta.object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

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
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUrlResponse:
    """Return a presigned URL (the browser then fetches straight from object storage), or this server's download URL for a store
    with no direct URL (the local folder)."""
    meta = await _get_meta(file_id, ctx, claims)
    store = ctx.files_for(claims.tenant_id, claims.sub)
    url = await store.presign_url(meta.object_key, expires_in=expires_in)

    if url.startswith("memory://"):
        url = f"/files/{file_id}/download"

    return FileUrlResponse(url=url, expires_in=expires_in)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> None:
    """Delete the file (the data service keeps its versions until they are purged) and its record."""
    meta = await _get_meta(file_id, ctx, claims)
    index = _index(ctx, claims)
    store = ctx.files_for(claims.tenant_id, claims.sub)

    # Same conversation-lock-on-delete as routes/workspace.py::delete_file — this route is the composer/document-manager's own
    # delete path, so it needs the identical safeguard: an agent shouldn't silently keep acting on a file this conversation's own
    # history references that's now gone.
    if meta.thread_id is not None:
        thread = await db.get(Thread, meta.thread_id)
        if thread is not None:
            thread.locked_at = datetime.now(timezone.utc)
            thread.locked_reason = (
                f"A file was deleted from this conversation's storage: "
                f"{meta.original_name}"
            )
        await db.commit()

    await store.delete(meta.object_key)
    await store.delete(f"{meta.object_key}.extracted.md")
    await index.remove(meta)

    # Gone from the conversation's documents too, so the model can no longer open or cite it. Best-effort: a failure here must not
    # surface as a failed delete, since the file itself is already gone from the store above.
    if ctx.library is not None and meta.thread_id is not None and meta.checksum_sha256:
        from substrate_cloud.documents_library import documents_collection

        collection = documents_collection(
            claims.tenant_id, claims.sub, str(meta.thread_id)
        )
        if collection is not None:
            try:
                await ctx.library.delete(
                    collection=collection,
                    document=ctx.library.document_id(
                        meta.original_name, meta.checksum_sha256
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to remove deleted file %s from the conversation's documents: %s",
                    file_id,
                    exc,
                )
