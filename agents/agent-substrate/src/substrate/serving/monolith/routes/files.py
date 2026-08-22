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
from datetime import datetime, timezone
from typing import Optional

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

from substrate.capabilities.storage.workspace import WorkspaceQuotaExceededError
from substrate.logger import setup_logging
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import FileMetadata, User
from substrate.serving.monolith.routes.chat_context import EXTRACTABLE_CONTENT_TYPES
from substrate.serving.monolith.security.deps import get_current_user
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


def _may_access(meta: FileMetadata, claims: AuthClaims) -> bool:
    """Ownership check for file bytes.

    Not a simple ``user_id == claims.sub`` — ``upload_file`` leaves
    ``user_id`` NULL whenever ``claims.sub`` isn't a UUID (see
    ``_ensure_user``'s caller above), so that check alone would lock those
    users out of files they uploaded themselves. The reliable signal is
    structural: ``object_key`` is built from ``claims.sub`` at upload time
    (``users/{sub}/...``), the same way ``routes/workspace.py::serve_file``
    already derives its access boundary. ``user_id`` is kept as a fallback
    for rows where that still resolves.
    """
    if claims.is_admin:
        return True
    same_tenant = meta.org_id in (None, claims.tenant_id)
    if meta.object_key.startswith(f"users/{claims.sub}/"):
        return same_tenant
    if meta.user_id is not None and str(meta.user_id) == claims.sub:
        return same_tenant
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
    if row is None or not _may_access(row, claims):
        raise HTTPException(status_code=404, detail="File not found")
    return row


def _pdf_page_count(data: bytes) -> int:
    """Cheap page count via pypdf — no OCR/layout model involved. Raises
    ValueError on a corrupt/unreadable PDF (caller turns that into a 422,
    same as any other malformed upload)."""
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(data)).pages)


async def _build_extracted_sidecar_text(
    data: bytes, name: str, content_type: str
) -> Optional[str]:
    """Best-effort, page-marked plain text for ``code_interpreter`` to read
    instead of re-parsing a PDF's raw bytes. Mirrors the extraction fallback
    chain in ``routes/chat_context.py::_extract_document_text`` (extraction
    service, then local pypdf), but keeps page boundaries — and any
    image/table captions the extraction service returned — instead of
    joining everything into one blob. Returns ``None`` when nothing could be
    extracted."""
    pages: list[tuple[int, str]] = []
    captions_by_page: dict[int, list[str]] = {}

    if settings.DOC_HANDLER_SERVICE_URL:
        from substrate.doc_handler.client import ExtractionClient

        client = ExtractionClient(
            base_url=settings.DOC_HANDLER_SERVICE_URL,
            auth_token=settings.DOC_HANDLER_AUTH_TOKEN,
            timeout_s=settings.DOC_HANDLER_TIMEOUT_S,
        )
        try:
            result = await client.extract(data, name, content_type)
        finally:
            await client.close()
        if result.success and result.pages:
            pages = [(p.page_number, p.text) for p in result.pages]
            for image in result.images:
                if image.caption and image.page_number is not None:
                    captions_by_page.setdefault(image.page_number, []).append(
                        image.caption
                    )

    if not pages and content_type == "application/pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [
                (i + 1, page.extract_text() or "")
                for i, page in enumerate(reader.pages)
            ]
        except Exception:
            return None

    if not pages:
        return None

    sections: list[str] = []
    for page_number, text in pages:
        sections.append(f"## Page {page_number}\n\n{text.strip()}")
        for caption in captions_by_page.get(page_number, []):
            sections.append(f"> Image/table caption: {caption}")
    return "\n\n".join(sections).strip() or None


async def _write_extracted_sidecar(
    ctx: ServerDependencies,
    file_id: uuid.UUID,
    data: bytes,
    *,
    object_key: str,
    original_name: str,
    content_type: str,
) -> None:
    """Write a ``{original_name}.extracted.md`` sidecar next to the uploaded
    file, in the same session workspace directory — so ``code_interpreter``
    (which mounts that same directory) can read pipeline-quality extracted
    text instead of pypdf-ing the raw PDF bytes itself. Best-effort: never
    raises, never affects staging success/failure."""
    try:
        if ctx.file_store is None:
            return
        text = await _build_extracted_sidecar_text(data, original_name, content_type)
        if not text:
            return
        sidecar_key = f"{object_key}.extracted.md"
        await ctx.file_store.upload(
            sidecar_key, text.encode("utf-8"), content_type="text/markdown"
        )
    except Exception as exc:
        logger.warning("Writing extracted sidecar failed for file %s: %s", file_id, exc)


async def _stage_uploaded_doc(
    ctx: ServerDependencies,
    file_id: uuid.UUID,
    data: bytes,
    *,
    object_key: str,
    original_name: str,
    content_type: str,
    owner_sub: str,
) -> None:
    """Fire-and-forget eager extraction+embedding, staged under a temporary
    collection — not the real thread collection (see
    ``LocalRagBackend.promote`` / ``routes/chat_context.py``). Same
    in-process ``asyncio.create_task`` pattern as ``routes/scheduled.py``'s
    ``_run_bg`` — no durable job queue in this codebase. If the server
    restarts mid-task, ``staged_at`` simply never gets set; the send-time
    path already handles that (blocks with a clear "still processing"
    error) rather than needing a retry queue."""
    assert ctx.rag_backend is not None
    session_factory = ctx.session_factory
    try:
        await ctx.rag_backend.ingest(
            data,
            collection=f"staging:{file_id}",
            metadata={
                "filename": original_name,
                "content_type": content_type,
                "file_id": str(file_id),
                # Lets the backend park extracted chart/table images under
                # users/{sub}/rag/... instead of inlining their bytes into
                # Postgres — see LocalRagBackend._ingest_images.
                "user_id": owner_sub,
            },
        )
    except Exception as exc:
        logger.warning("Eager staging failed for file %s: %s", file_id, exc)
        async with session_factory() as session:
            row = await session.get(FileMetadata, file_id)
            if row is not None:
                row.staging_error = str(exc)[:500]
                await session.commit()
        return
    await _write_extracted_sidecar(
        ctx,
        file_id,
        data,
        object_key=object_key,
        original_name=original_name,
        content_type=content_type,
    )
    async with session_factory() as session:
        row = await session.get(FileMetadata, file_id)
        if row is not None:
            row.staged_at = datetime.now(timezone.utc)
            await session.commit()


@router.post("/upload", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    request: Request,
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

    RAG-eligible types (currently PDF only — see ``EXTRACTABLE_CONTENT_TYPES``)
    get extra, synchronous-before-storing checks (upload-attempt quota, size
    cap, page cap) plus eager background staging (extraction+embedding into
    a temporary collection) once stored — see ``_stage_uploaded_doc``. Other
    file types are unaffected: pure blob+metadata storage, same as today.
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

    page_count: Optional[int] = None
    is_extractable = content_type in EXTRACTABLE_CONTENT_TYPES
    will_stage = (
        is_extractable
        and ctx.rag_backend is not None
        and ctx.rag_backend.name == "local"
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
        page_count=page_count,
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
            )
        )

    return FileUploadResponse(
        id=meta.id,
        thread_id=meta.thread_id,
        name=meta.original_name,
        mime=meta.content_type,
        size=meta.size_bytes,
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
    key: str = Query(..., description="Object key under the caller's users/{sub}/"),
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Serve a stored object by key — the target of the ``/files/object?key=``
    links in tool-result attachments (see ``agents/runtime/context/tool.py``).

    Deliberately a stable, authenticated app URL rather than a presigned one:
    the wire-event log is replayed months later, and a presigned link would
    have expired, leaving old conversations full of dead images.

    Ownership is the key's own ``users/{sub}/`` prefix, so a caller can only
    ever read their own objects. A mismatch 404s rather than 403s — same
    rationale as ``_get_meta`` above: this must not become an existence oracle
    for keys that leak into logs or URLs.

    Registered before ``/{file_id}/status`` so "object" is never matched as a
    file_id path param (Starlette matches in registration order).
    """
    if not key.startswith(f"users/{claims.sub}/"):
        raise HTTPException(status_code=404, detail="Not found")
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File storage is not configured")
    try:
        data = await ctx.file_store.download(key)
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Download file bytes."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta = await _get_meta(file_id, db, claims)
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
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> FileUrlResponse:
    """Return a presigned URL (or download URL for InMemoryFileStore)."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta = await _get_meta(file_id, db, claims)
    url = await ctx.file_store.presign_url(meta.object_key, expires_in=expires_in)

    if url.startswith("memory://"):
        url = f"/files/{file_id}/download"

    return FileUrlResponse(url=url, expires_in=expires_in)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: uuid.UUID,
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> None:
    """Soft-delete metadata and remove object from store."""
    if ctx.file_store is None:
        raise HTTPException(status_code=503, detail="File store not configured")

    meta = await _get_meta(file_id, db, claims)
    meta.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    await ctx.file_store.delete(meta.object_key)

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
                user_id=claims.sub, file_id=str(file_id)
            )
        except Exception as exc:
            logger.warning(
                "Failed to clean up RAG images for deleted file %s: %s", file_id, exc
            )
