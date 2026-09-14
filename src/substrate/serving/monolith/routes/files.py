"""File upload / download / presign / delete endpoints.

Routes:
  POST   /files/upload           – upload a file; returns FileUploadResponse
  GET    /files/{file_id}/download – stream raw bytes back
  GET    /files/{file_id}/url      – presigned (or download) URL
  DELETE /files/{file_id}          – soft-delete metadata + delete from store
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.capabilities.storage.layout import conversation_shared_key, user_prefix
from substrate.capabilities.storage.workspace import WorkspaceQuotaExceededError
from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.logger import setup_logging
from substrate.serving.monolith.security.rls_deps import get_tenant_scoped_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import FileMetadata, Thread, User
from substrate.serving.monolith.routes.chat_context import (
    EXTRACTABLE_CONTENT_TYPES,
    _session_relative_path,
)
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.monolith.services import get_owned_thread
from substrate.serving.shared.auth.claims import AuthClaims
from substrate.serving.shared.contracts.file_store import (
    FileUploadResponse,
    FileUrlResponse,
)
from substrate.serving.shared.doc_quota import (
    check_and_increment,
    peek,
    seconds_until_reset,
)
from substrate.serving.shared.settings import settings
from substrate.runtimes.document_intelligence.extract import extract_document
from substrate.runtimes.document_intelligence.service.types import ExtractionResult

logger = setup_logging()

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


async def _may_access(meta: FileMetadata, claims: AuthClaims, db: AsyncSession) -> bool:
    """Ownership check for file bytes.

    Not a simple ``user_id == claims.sub`` — ``upload_file`` leaves
    ``user_id`` NULL whenever ``claims.sub`` isn't a UUID (see
    ``_ensure_user``'s caller above), so that check alone would lock those
    users out of files they uploaded themselves. The reliable signal is
    tenant and user/thread columns are the authority; paths are intentionally
    not used for authorization because paths are storage implementation
    details, not identity claims.

    Was previously ``user_id`` only, with an unconditional ``return False``
    whenever it was NULL — directly contradicting this docstring's own
    stated intent, and blocking every non-UUID-sub (e.g. anonymous
    ``anon-<uuid>``) caller from ever reading back their own upload's
    status/bytes/delete, found live: a just-uploaded file's own composer
    polling 404'd in an infinite loop, the file stuck showing "Processing…"
    forever. Fixed by falling back to thread ownership (same check
    ``_may_access_key`` already uses for conversation-scoped artifacts)
    when ``user_id`` is NULL but the file has a ``thread_id`` — real
    identity evidence (``Thread.user_identifier == claims.sub``), not a
    tenant-wide bypass that would let any same-tenant anonymous visitor
    read any other's files.
    """
    if claims.is_admin:
        return True
    same_tenant = meta.org_id == claims.tenant_id
    if not same_tenant:
        return False
    if meta.user_id is not None:
        return str(meta.user_id) == claims.sub
    if meta.thread_id is not None:
        owned = (
            await db.execute(
                select(Thread.id).where(
                    Thread.id == meta.thread_id,
                    Thread.user_identifier == claims.sub,
                    Thread.tenant_id == claims.tenant_id,
                )
            )
        ).scalar_one_or_none()
        return owned is not None
    return False


async def _may_access_key(key: str, claims: AuthClaims, db: AsyncSession) -> bool:
    """Ownership check for an object key with no ``FileMetadata`` row at
    all — RAG-extracted images and other capability-written artifacts are
    uploaded straight to the store with no metadata row (see
    ``capabilities/knowledge/backends/local.py::_store_image_bytes``), so
    ``serve_object`` cannot rely on ``_may_access`` for them. Structural,
    not a raw prefix trust: every key starts ``tenants/{tenant_id}/...``,
    where ``tenant_id`` came from the server's own JWT-minted keys, never
    from this request — so checking it is checking real ownership, not an
    attacker-controlled string.
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


async def _get_meta(
    file_id: uuid.UUID,
    db: AsyncSession,
    claims: AuthClaims,
) -> FileMetadata:
    row = (
        await db.execute(
            select(FileMetadata).where(
                FileMetadata.id == file_id,
                FileMetadata.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    # 404 whether the row is missing or just not this caller's — never a
    # distinct 403, so this endpoint isn't an existence oracle for file ids
    # that happen to leak into logs/URLs (same rationale as
    # thread_service.get_owned_thread).
    if row is None or not await _may_access(row, claims, db):
        raise HTTPException(status_code=404, detail="File not found")
    return row


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
                captions_by_page.setdefault(image.page_number, []).append(
                    image.caption
                )

    sections: list[str] = []
    for page in result.pages:
        sections.append(f"<!-- page {page.page_number} -->\n{page.text.strip()}")
        for caption in captions_by_page.get(page.page_number, []):
            sections.append(f"> Image/table caption: {caption}")
    return "\n\n".join(sections).strip() or None


async def _build_extracted_sidecar_text(
    data: bytes, name: str, content_type: str
) -> Optional[str]:
    """Best-effort, page-marked plain text for ``code_interpreter`` to read
    instead of re-parsing a PDF's raw bytes. Delegates the extraction
    fallback chain (document-intelligence service, else local pypdf/etc.) to
    ``extract_document``, then renders the result via
    ``render_page_marked_markdown``.
    """
    endpoint = None
    if settings.DOCUMENT_INTELLIGENCE_SERVICE_URL:
        endpoint = InferenceEndpoint(
            model="",
            base_url=settings.DOCUMENT_INTELLIGENCE_SERVICE_URL,
            api_key=settings.DOCUMENT_INTELLIGENCE_AUTH_TOKEN,
            timeout_s=settings.DOCUMENT_INTELLIGENCE_TIMEOUT_S,
        )
    result = await extract_document(data, name, content_type, endpoint=endpoint)
    return render_page_marked_markdown(result)


async def _write_extracted_sidecar(
    store: Any,
    file_id: uuid.UUID,
    data: bytes,
    *,
    object_key: str,
    original_name: str,
    content_type: str,
) -> None:
    """Write a ``{original_name}.extracted.md`` sidecar next to the uploaded
    file, in the same *store* the raw file itself currently lives in — the
    pending store pre-promotion, ``ctx.file_store`` after (see
    ``capabilities/storage/pending.py``) — so ``code_interpreter`` (which
    mounts that same directory once promoted) can read pipeline-quality
    extracted text instead of pypdf-ing the raw PDF bytes itself.
    Best-effort: never raises, never affects staging success/failure."""
    try:
        if store is None:
            return
        text = await _build_extracted_sidecar_text(data, original_name, content_type)
        if not text:
            return
        sidecar_key = f"{object_key}.extracted.md"
        await store.upload(
            sidecar_key, text.encode("utf-8"), content_type="text/markdown"
        )
    except Exception as exc:
        logger.warning("Writing extracted sidecar failed for file %s: %s", file_id, exc)


async def _set_tenant_guc(session: AsyncSession, tenant_id: str) -> None:
    """Set the RLS session GUC on a session created directly from
    ``session_factory()`` — i.e. one that never went through
    ``get_tenant_scoped_db`` (no FastAPI request/DI context exists for a
    fire-and-forget background task like ``_stage_uploaded_doc``).

    Without this, ``file_metadata``'s ``FORCE ROW LEVEL SECURITY`` policy
    (``org_id = current_setting('app.current_tenant_id', true)``) hides
    every row from this session — found live: ``ingest_session_document``
    ran to real completion (confirmed via Lance table data actually
    written to disk), but the follow-up ``session.get(FileMetadata,
    file_id)`` below silently returned ``None`` and the caller's `if row
    is not None` guard swallowed it with no error — so `staged_at` /
    `staging_error` never got written, and the composer polled "still
    processing" forever for a file that had, in fact, finished.
    """
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, false)"),
        {"tid": tenant_id},
    )


async def _stage_uploaded_doc(
    ctx: ServerDependencies,
    file_id: uuid.UUID,
    data: bytes,
    *,
    object_key: str,
    original_name: str,
    content_type: str,
    owner_sub: str,
    tenant_id: str,
    session_id: str,
) -> None:
    """Fire-and-forget eager extraction+embedding into the caller's own
    per-user session-document index (vector + PageIndex tree + knowledge
    graph — see ``capabilities/knowledge/session_ingest.py``), already
    tagged with the real ``session_id`` — unlike the old Postgres
    staging-collection flow this replaced, there's no separate "promote"
    data-movement step needed later: every row is written already scoped
    to where it belongs. Same in-process ``asyncio.create_task`` pattern as
    ``routes/scheduled.py``'s ``_run_bg`` — no durable job queue in this
    codebase. If the server restarts mid-task, ``staged_at`` simply never
    gets set; the send-time path already handles that (blocks with a clear
    "still processing" error) rather than needing a retry queue."""
    assert ctx.rag_backend is not None
    assert ctx.embedding_client is not None
    session_factory = ctx.session_factory
    from substrate.capabilities.knowledge.session_ingest import ingest_session_document

    try:
        await ingest_session_document(
            data=data,
            filename=original_name,
            content_type=content_type,
            tenant_id=tenant_id,
            user_id=owner_sub,
            session_id=session_id,
            cfg=settings,
            embedding_client=ctx.embedding_client,
            model_client=ctx.model_client,
            rag_backend=ctx.rag_backend,
        )
    except Exception as exc:
        # Full detail goes to the server log only — staging_error is served
        # straight to the UI as-is (routes/files.py's status endpoint), and
        # the raw exception text (a CUDA OOM traceback, a stack trace, an
        # internal service's error body) is neither meaningful nor safe to
        # show a user: it leaks infra internals and reads as broken, not as
        # something they can act on.
        logger.warning("Eager staging failed for file %s: %s", file_id, exc)
        async with session_factory() as session:
            await _set_tenant_guc(session, tenant_id)
            row = await session.get(FileMetadata, file_id)
            if row is not None:
                row.staging_error = (
                    "Couldn't process this document automatically. "
                    "It may be too large or complex — try a smaller file, "
                    "or ask the assistant about it directly."
                )
                await session.commit()
        return
    await _write_extracted_sidecar(
        ctx.pending_file_store,
        file_id,
        data,
        object_key=object_key,
        original_name=original_name,
        content_type=content_type,
    )
    async with session_factory() as session:
        await _set_tenant_guc(session, tenant_id)
        row = await session.get(FileMetadata, file_id)
        if row is not None:
            row.staged_at = datetime.now(timezone.utc)
            await session.commit()


async def promote_pending_file(ctx: ServerDependencies, meta: FileMetadata) -> None:
    """Copy an attachment from the local pending store into the real
    ``ctx.file_store`` (SeaweedFS/S3) and mark it promoted — called once,
    at the moment a message that actually references this file is sent
    (see ``routes/chat_context.py::_build_file_context``). Idempotent
    no-op if already promoted, since a send can reference the same file
    more than once (e.g. across retries)."""
    if meta.promoted_at is not None:
        return
    if ctx.pending_file_store is None or ctx.file_store is None:
        return
    data = await ctx.pending_file_store.download(meta.object_key)
    await ctx.file_store.upload(meta.object_key, data, content_type=meta.content_type)
    await ctx.pending_file_store.delete(meta.object_key)
    sidecar_key = f"{meta.object_key}.extracted.md"
    if await ctx.pending_file_store.exists(sidecar_key):
        sidecar_data = await ctx.pending_file_store.download(sidecar_key)
        await ctx.file_store.upload(
            sidecar_key, sidecar_data, content_type="text/markdown"
        )
        await ctx.pending_file_store.delete(sidecar_key)
    meta.promoted_at = datetime.now(timezone.utc)


async def sweep_stale_pending_uploads(
    session_factory: Any, ttl_hours: float
) -> int:
    """Delete ``FileMetadata`` rows for attachments abandoned before ever
    being sent — never promoted, older than *ttl_hours*. Their bytes are
    already gone (or about to be, via ``PendingFileStore.sweep_stale``,
    same TTL) — this is the DB-row half of that cleanup, run once at
    startup from ``app.py``. Cross-tenant by design (an admin/startup-time
    sweep, not a request), so it sets ``app.bypass_rls`` rather than a
    single tenant's GUC — same pattern ``get_tenant_scoped_db`` uses for
    admin/service callers."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    async with session_factory() as session:
        await session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        result = await session.execute(
            select(FileMetadata.id).where(
                FileMetadata.promoted_at.is_(None),
                FileMetadata.deleted_at.is_(None),
                FileMetadata.created_at < cutoff,
            )
        )
        stale_ids = [row[0] for row in result.all()]
        if not stale_ids:
            return 0
        await session.execute(
            FileMetadata.__table__.delete().where(FileMetadata.id.in_(stale_ids))
        )
        await session.commit()
    return len(stale_ids)


@router.post("/upload", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    thread_id: Optional[uuid.UUID] = Form(None),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUploadResponse:
    """Upload a file and store its metadata.

    Object keys are scoped by tenant, user, and thread (when given):
    ``tenants/{tid}/users/{uid}/conversations/{thread_id}/workspace/shared/
    uploads/{name}`` or, with no thread, ``tenants/{tid}/users/{uid}/
    uploads/{name}`` — see ``capabilities/storage/layout.py``. This is the
    same prefix the code interpreter's sandbox mounts for that thread, so a
    thread-scoped upload lands exactly where that thread's sandbox session
    can see it.

    RAG-eligible types (currently PDF only — see ``EXTRACTABLE_CONTENT_TYPES``)
    get extra, synchronous-before-storing checks (upload-attempt quota, size
    cap, page cap) plus eager background staging (extraction, chunking,
    embedding, PageIndex tree, and graph extraction into the caller's
    per-user session-document index) once stored, when a ``thread_id`` is
    already known — see ``_stage_uploaded_doc``. Other file types are
    unaffected: pure blob+metadata storage, same as today.
    """
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    if thread_id is not None and await get_owned_thread(db, thread_id, claims) is None:
        # Without this, any authenticated caller could tag an upload with
        # someone else's thread_id: the bytes land in the caller's own
        # prefix (harmless there), but the FileMetadata row still carries
        # that thread_id — a confused-deputy path into a conversation the
        # attacker doesn't own if anything trusts FileMetadata.thread_id
        # for context/attachment lookups without re-checking ownership.
        raise HTTPException(status_code=404, detail="Thread not found")

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
    will_stage = (
        is_extractable
        and ctx.rag_backend is not None
        and ctx.rag_backend.name == "local"
        and ctx.embedding_client is not None
        # No thread_id yet -> no session_id to scope the per-user index
        # under. Deferred to send time instead, once the real thread_id is
        # known — see chat_context.py::_build_file_context.
        and thread_id is not None
    )
    if is_extractable:
        # The upload-attempt quota specifically bounds eager-staging compute
        # abuse (repeated upload-then-discard) — only meaningful when eager
        # staging actually runs (local backend). Pinecone stays lazy-on-send
        # as it always has, so there's no matching compute cost to bound
        # here; skip straight to the (backend-agnostic) size/page hygiene
        # checks below.
        if will_stage:
            redis = getattr(request.app.state, "redis", None)
            if redis is not None:
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
        max_doc_bytes = settings.RAG_MAX_DOC_MB * 1024 * 1024
        if len(data) > max_doc_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Document exceeds maximum size of {settings.RAG_MAX_DOC_MB} MB",
            )
        if content_type == "application/pdf":
            try:
                page_count = _pdf_page_count(data)
            except Exception as exc:
                raise HTTPException(
                    status_code=422, detail=f"Could not read PDF: {exc}"
                ) from exc
            if page_count > settings.RAG_MAX_DOC_PAGES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Document has {page_count} pages, exceeding the "
                        f"{settings.RAG_MAX_DOC_PAGES}-page limit."
                    ),
                )

    if thread_id is not None:
        base_key = conversation_shared_key(
            claims.tenant_id, claims.sub, str(thread_id), f"uploads/{original_name}"
        )
    else:
        base_key = (
            f"{user_prefix(claims.tenant_id, claims.sub)}/uploads/{original_name}"
        )
    object_key = await _unique_object_key(db, base_key)

    if thread_id is not None:
        # A chat-composer attachment — stays local-disk-only, never
        # touching SeaweedFS/S3, until the message carrying it is actually
        # sent (routes/chat_context.py promotes it then; see
        # capabilities/storage/pending.py's module docstring for why).
        if ctx.pending_file_store is None:
            raise HTTPException(
                status_code=503, detail="Pending upload storage not configured"
            )
        await ctx.pending_file_store.upload(object_key, data, content_type=content_type)
        promoted_at = None
    else:
        # No thread_id -> not a composer attachment (e.g. the settings
        # Storage tab's standalone file manager) -> no chat "Send" event
        # will ever exist to promote it out of a pending store, so it
        # would sit there until the abandoned-upload sweep quietly deleted
        # it. Go straight to permanent storage — already final.
        if ctx.file_store is None:
            raise HTTPException(status_code=503, detail="File store not configured")
        try:
            await ctx.file_store.upload(object_key, data, content_type=content_type)
        except WorkspaceQuotaExceededError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        promoted_at = datetime.now(timezone.utc)

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
        page_count=page_count,
        promoted_at=promoted_at,
    )
    db.add(meta)
    await db.commit()
    await db.refresh(meta)

    if will_stage and ctx.session_factory is not None:
        asyncio.create_task(
            _stage_uploaded_doc(
                ctx,
                meta.id,
                data,
                object_key=object_key,
                original_name=original_name,
                content_type=content_type,
                owner_sub=claims.sub,
                tenant_id=claims.tenant_id,
                session_id=str(thread_id),
            )
        )

    return FileUploadResponse(
        id=meta.id,
        thread_id=meta.thread_id,
        name=meta.original_name,
        mime=meta.content_type,
        size=meta.size_bytes,
        # object_key is fixed at upload time regardless of promotion state
        # (see conversation_shared_key(...) above), so this can be computed
        # immediately rather than waiting for the first chat turn. Lets the
        # composer's optimistic just-sent render open the file in the
        # read-only viewer right away, instead of only after a reload
        # picks up chat_context.py's own (identically-derived) session_path
        # on the persisted message.
        session_path=(
            _session_relative_path(object_key) if thread_id is not None else None
        ),
    )


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
    """Serve a stored object by key — the target of the ``/files/object?key=``
    links in tool-result attachments (see ``agents/runtime/context/tool.py``)
    and of ``ask()``'s ``Citation.image_key``/``pdf_key`` (see
    ``capabilities/knowledge/ask.py``).

    Deliberately a stable, authenticated app URL rather than a presigned one:
    the wire-event log is replayed months later, and a presigned link would
    have expired, leaving old conversations full of dead images.

    Authorization is resolved through a ``FileMetadata`` row when one exists
    (uploads go through ``upload_file``, which records one); many objects
    this route serves never get one — RAG-extracted images, other
    capability-written artifacts — so those fall back to ``_may_access_key``,
    a structural check on the key's own tenant/user/conversation segments.
    In particular, knowledge-base keys are project-shared within a tenant,
    never a global open namespace across tenants.

    Registered before ``/{file_id}/status`` so "object" is never matched as a
    file_id path param (Starlette matches in registration order).
    """
    meta = (
        await db.execute(
            select(FileMetadata).where(
                FileMetadata.object_key == key,
                FileMetadata.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if meta is not None:
        allowed = await _may_access(meta, claims, db)
    else:
        allowed = await _may_access_key(key, claims, db)
    if not allowed:
        raise HTTPException(status_code=404, detail="Not found")
    # Normally unreachable (citations/tool-result links only exist once a
    # message has already been sent, which is what promotes a file out of
    # the pending store) — defensive fallback, not a designed path.
    store = (
        ctx.pending_file_store
        if meta is not None and meta.promoted_at is None
        else ctx.file_store
    )
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
    db: AsyncSession = Depends(get_tenant_scoped_db),
) -> dict:
    """Lightweight polling target for the composer's per-attachment progress
    ring — never touches file_store, just the metadata row. See
    ``substrate-ui``'s ``useFileAttachments.ts``: ``page_count``/
    ``created_at`` drive a simulated (elapsed-time-based) progress estimate
    since true per-page extraction progress isn't available (PaddleOCR
    batches internally despite ``predict_iter()`` looking lazy — verified,
    not assumed); ``staged_at``/``staging_error`` are the real ground truth
    for when the ring should snap to 100% or show an error state."""
    meta = await _get_meta(file_id, db, claims)
    return {
        "staged_at": meta.staged_at,
        "staging_error": meta.staging_error,
        "page_count": meta.page_count,
        "created_at": meta.created_at,
    }


@router.get("/{file_id}/download")
async def download_file(
    file_id: uuid.UUID,
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Download file bytes."""
    meta = await _get_meta(file_id, db, claims)
    store = ctx.file_store if meta.promoted_at is not None else ctx.pending_file_store
    if store is None:
        raise HTTPException(status_code=503, detail="File store not configured")
    data = await store.download(meta.object_key)

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
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUrlResponse:
    """Return a presigned URL (or download URL for InMemoryFileStore/not-yet-
    promoted pending attachments — the pending store has no presign
    capability, so it always falls back to the redirect-through-us form)."""
    meta = await _get_meta(file_id, db, claims)
    if meta.promoted_at is None:
        return FileUrlResponse(url=f"/files/{file_id}/download", expires_in=expires_in)
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    url = await ctx.file_store.presign_url(meta.object_key, expires_in=expires_in)

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
    """Soft-delete metadata and remove object from store."""
    meta = await _get_meta(file_id, db, claims)
    store = ctx.file_store if meta.promoted_at is not None else ctx.pending_file_store
    if store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta.deleted_at = datetime.now(timezone.utc)

    # Same conversation-lock-on-delete as routes/workspace.py::delete_file —
    # this route is the composer/document-manager's own delete path, so it
    # needs the identical safeguard: an agent shouldn't silently keep acting
    # on a file this conversation's own history references that's now gone.
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
    if meta.promoted_at is None and ctx.pending_file_store is not None:
        await ctx.pending_file_store.delete(f"{meta.object_key}.extracted.md")

    # Discarded before ever being sent (rag_ingested_at never set) — clean
    # up its orphaned staging collection so it doesn't linger forever.
    # Best-effort: a failure here must not surface as a failed delete, since
    # the file itself is already gone from the store above.
    if (
        ctx.rag_backend is not None
        and ctx.rag_backend.name == "local"
        and meta.rag_ingested_at is None
    ):
        try:
            await ctx.rag_backend.delete_collection(f"staging:{file_id}")
        except Exception as exc:
            logger.warning(
                "Failed to clean up staging collection for deleted file %s: %s",
                file_id,
                exc,
            )
        # The vector rows are gone; their image objects would otherwise linger
        # in storage against the owner's quota with nothing pointing at them.
        try:
            await ctx.rag_backend.delete_file_images(
                tenant_id=claims.tenant_id, user_id=claims.sub, file_id=str(file_id)
            )
        except Exception as exc:
            logger.warning(
                "Failed to clean up RAG images for deleted file %s: %s", file_id, exc
            )
