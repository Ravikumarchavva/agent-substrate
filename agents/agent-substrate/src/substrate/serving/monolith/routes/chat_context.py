"""Chat per-request dependency + file-context assembly.

Split out of ``chat.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.infrastructure.serving_factory import build_chat_tools
from substrate.serving.monolith.dependencies import ServerDependencies
from substrate.serving.monolith.schemas import ChatRequest
from substrate.serving.monolith.routes.chat_wire import _ImagePayload
from substrate.serving.shared.auth.claims import AuthClaims
from substrate.serving.shared.doc_quota import check_and_increment, release
from substrate.serving.shared.settings import settings
from substrate.logger import setup_logging

logger = setup_logging()


async def _get_agent_deps(ctx: ServerDependencies, thread_id: str):
    """Assemble per-request agent dependencies with an isolated HITL bridge."""
    bridge = await ctx.bridge_registry.acquire(str(thread_id))
    # Cancel any signal-based HITL from a prior run on this thread so the old
    # suspended run can finish cleanly (tool_use → tool_result stays balanced).
    await bridge.cancel_signal_requests("new_message")
    tools = build_chat_tools(ctx.tools, bridge)
    return {
        "model_client": ctx.model_client,
        "tools": tools,
        "system_instructions": ctx.system_instructions,
        "tools_requiring_approval": ctx.tools_requiring_approval,
        "tool_timeout": ctx.tool_timeout,
        "bridge": bridge,
        "runtime": ctx.runtime,
    }


# Both the K8s agent-sandbox pod and the local sandbox container mount the
# workspace at this exact path (see sandbox_service.py's
# workspace_mount_path default and deployment/docker/docker-compose.yml's
# code-interpreter-sandbox volume). sandbox_runtime.py's /ci/run changes cwd
# to WORKSPACE_DIR/sessions/{session_id} for every run — nothing currently
# threads the real chat thread_id into that session_id for direct tool
# calls (it defaults to "default"), so a workspace-relative path would
# resolve against the wrong directory. An absolute path sidesteps that
# entirely regardless of cwd.
_SANDBOX_WORKSPACE_MOUNT_PATH = "/app/workspace"

# The extraction service (PaddleOCR-based) reads PDF and raster images
# natively — no DOCX/PPTX parser (verified: paddlex has no docx/pptx reader
# at all). Those formats stay metadata-only, same as when no extraction
# service is configured. Public (not `_`-prefixed): also imported by
# routes/files.py to scope upload-time page/size caps and eager staging to
# the same set of types this module actually ingests.
#
# text/markdown: NOT routed through the extraction service at all —
# LocalRagBackend._load() already dispatches .txt/.md straight to the local
# TextLoader (no OCR, no PaddleOCR call — see backends/local.py's
# _LOCAL_FALLBACK_EXTENSIONS). Added here purely to make files.py's existing
# will_stage/_stage_uploaded_doc eligibility check include it, so a large
# paste-to-document upload gets chunked+embedded+staged the same way a PDF
# does — same mechanism, not a new one.
EXTRACTABLE_CONTENT_TYPES = {"application/pdf", "text/markdown"}


def _session_relative_path(object_key: str) -> str | None:
    """``users/{uid}/sessions/{tid}/{rest}`` → ``rest``, or ``None`` if
    *object_key* isn't a thread-scoped upload (e.g. ``users/{uid}/uploads/...``).

    Shared by the bubblewrap workspace-path branch of ``_attachment_dict``
    below and by the RAG-ingest metadata: both need the path a citation's
    "open this file" click uses (``routes/workspace.py::serve_file``),
    relative to the thread's session dir — never ``original_name``, which can
    differ from the real object-key basename when ``_unique_object_key``
    (``routes/files.py``) appended a uniquifying suffix.
    """
    parts = object_key.split("/", 4)
    if len(parts) == 5 and parts[0] == "users" and parts[2] == "sessions":
        return parts[4]
    return None


def _truncate(text: str) -> str:
    max_chars = settings.ATTACHMENT_PDF_MAX_CHARS
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[...truncated to {max_chars} characters]"
    return text


async def _extract_via_pypdf(data: bytes, name: str) -> str | None:
    """pypdf/pdfplumber fallback — PDF only, no extraction-service dependency."""
    from substrate.capabilities.knowledge.loaders.pdf_loader import PDFLoader
    from substrate.kernel.core.content import TextBlock

    try:
        docs = await PDFLoader().load(data, metadata={"source": name})
    except Exception:
        logger.warning("PDF text extraction failed for %r", name, exc_info=True)
        return None

    text = "\n\n".join(
        block.text
        for doc in docs
        for block in doc.content
        if isinstance(block, TextBlock)
    ).strip()
    return text or None


async def _extract_document_text(
    data: bytes, name: str, content_type: str
) -> tuple[str | None, str | None]:
    """Extract text for inlining into the prompt. Returns ``(text, engine)``;
    ``(None, None)`` means extraction failed entirely — the caller falls
    back to metadata-only attachment handling in that case.

    Tries the document-intelligence service first (layout-aware: chart/table
    detection, OCR) when ``DOCUMENT_INTELLIGENCE_SERVICE_URL`` is configured.
    Falls back to the lightweight local pypdf/pdfplumber path for PDFs on
    any extraction failure/timeout/non-configuration.
    """
    if settings.DOCUMENT_INTELLIGENCE_SERVICE_URL:
        from substrate.runtimes.document_intelligence.client import (
            ExtractionClient,
        )

        client = ExtractionClient(
            base_url=settings.DOCUMENT_INTELLIGENCE_SERVICE_URL,
            auth_token=settings.DOCUMENT_INTELLIGENCE_AUTH_TOKEN,
            timeout_s=settings.DOCUMENT_INTELLIGENCE_TIMEOUT_S,
        )
        try:
            result = await client.extract(data, name, content_type)
        finally:
            await client.close()
        if result.success and result.text.strip():
            return _truncate(result.text.strip()), "paddleocr"
        logger.info(
            "Extraction unavailable/failed for %r (%s); falling back",
            name,
            result.error,
        )

    if content_type == "application/pdf":
        text = await _extract_via_pypdf(data, name)
        if text is not None:
            return _truncate(text), "pypdf"

    return None, None


async def _build_file_context(
    db: AsyncSession,
    body: ChatRequest,
    request: Request,
    ctx: ServerDependencies,
    claims: AuthClaims,
) -> tuple[str, list[_ImagePayload], list[dict[str, Any]]]:
    """Resolve file_ids to text/image/attachment context for the chat turn."""
    if not body.file_ids or ctx.file_store is None:
        return "", [], []

    from sqlalchemy import select

    from substrate.serving.monolith.models import FileMetadata

    rows = (
        (
            await db.execute(
                select(FileMetadata).where(
                    FileMetadata.id.in_(body.file_ids),
                    FileMetadata.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    # Pre-validation pass, staged/local-backend files only: block the WHOLE
    # send (not a silent per-file degrade — this session's explicit design
    # choice) if any referenced file failed eager staging, is still
    # processing, or would push the caller over today's commit quota.
    # Raised before any part of the turn proceeds, so a bad file never
    # results in a partially-built prompt. The frontend is expected to
    # avoid hitting this in the common case (queued send — see
    # substrate-ui's composer), so a live 425 here should be rare; it's a
    # defense-in-depth backstop, not the primary UX.
    if ctx.rag_backend is not None and ctx.rag_backend.name == "local":
        new_commits = [
            m
            for m in rows
            if m.content_type in EXTRACTABLE_CONTENT_TYPES and m.rag_ingested_at is None
        ]
        if new_commits:
            for meta in new_commits:
                if meta.staging_error:
                    raise HTTPException(
                        status_code=422,
                        detail=f"'{meta.original_name}' failed to process: {meta.staging_error}",
                    )
                if meta.staged_at is None:
                    raise HTTPException(
                        status_code=425,
                        detail=f"'{meta.original_name}' is still processing — try again in a moment.",
                    )
            redis = getattr(request.app.state, "redis", None)
            if redis is not None:
                allowed, _remaining = await check_and_increment(
                    redis,
                    "docquota:commit",
                    claims.sub,
                    settings.RAG_DAILY_DOC_LIMIT,
                    count=len(new_commits),
                )
                if not allowed:
                    await release(
                        redis, "docquota:commit", claims.sub, count=len(new_commits)
                    )
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"Daily document limit ({settings.RAG_DAILY_DOC_LIMIT}) "
                            "reached — try again tomorrow."
                        ),
                    )

    text_parts: list[str] = []
    image_inputs: list[_ImagePayload] = []
    attachments: list[dict[str, Any]] = []
    needs_commit = False

    def _attachment_dict(meta: Any) -> dict[str, Any]:
        attachment: dict[str, Any] = {
            "id": str(meta.id),
            "name": meta.original_name,
            "mime": meta.content_type,
            "size": meta.size_bytes,
        }
        # object_key is "users/{uid}/sessions/{tid}/name" under the
        # WorkspaceFileStore default. What that maps to *inside* the sandbox
        # depends on how the active code interpreter mounts the workspace —
        # these backends use different mount topologies (mirrors the
        # ci_has_workspace_access gate below) — and the result is always
        # made absolute since the sandbox's execution cwd isn't guaranteed to
        # be the workspace root.
        relative_path: str | None = None
        mount_path = _SANDBOX_WORKSPACE_MOUNT_PATH
        # Extractable types (PDF today) are already ingested into the RagBackend
        # and readable via knowledge_search — never also hand the model a real,
        # working code_interpreter path to the same file. Offering both isn't
        # harmless redundancy: a prose instruction to "prefer knowledge_search"
        # loses every time to a concrete, correct absolute path sitting right
        # next to it, because reading the raw file *feels* more certain than a
        # semantic-search result even when it isn't. Observed directly — the
        # model reached for `pypdf.PdfReader(workspace_path)` on an already-
        # successfully-searched document, repeatedly, specifically because this
        # hint told it that path existed and was readable. Non-extractable types
        # (csv, images, etc.) still get the hint below — for those,
        # code_interpreter genuinely is the only way to read the file.
        if meta.content_type in EXTRACTABLE_CONTENT_TYPES:
            return attachment
        if settings.SANDBOX_RUNTIME == "bubblewrap":
            # Bubblewrap mounts ONLY the caller's own session dir — see
            # CodeInterpreterTool._session_dir — at /workspace, so the
            # "users/{uid}/sessions/{tid}/" prefix must be stripped entirely,
            # not just the "users/{uid}/" part.
            mount_path = "/workspace"
            relative_path = _session_relative_path(meta.object_key)
        elif settings.CI_WORKSPACE_PVC_CLAIM:
            # K8s agent-sandbox subPath-mounts "users/{uid}" at
            # /app/workspace (per-user pod — that subPath IS the isolation
            # boundary), so the prefix must be stripped.
            parts = meta.object_key.split("/", 2)
            if len(parts) == 3 and parts[0] == "users":
                relative_path = parts[2]
        if relative_path is not None:
            attachment["workspace_path"] = f"{mount_path}/{relative_path}"
        return attachment

    for meta in rows:
        if meta.content_type in EXTRACTABLE_CONTENT_TYPES:
            # Extractable docs are ingested into the thread's RagBackend
            # collection instead of inlined into the prompt — the agent
            # retrieves relevant passages via the knowledge_search tool.
            # Cache hit: already ingested (files are immutable once
            # uploaded), skip re-ingesting on every later reference.
            if ctx.rag_backend is not None:
                if meta.rag_ingested_at is None:
                    if ctx.rag_backend.name == "local" and meta.staged_at is not None:
                        # Already extracted+embedded eagerly at upload time
                        # (routes/files.py) — cheap re-key into the real
                        # thread collection, no re-extraction/re-embedding.
                        # The pre-validation pass above already confirmed
                        # staging succeeded and quota was consumed for this
                        # file before we got here.
                        try:
                            await ctx.rag_backend.promote(
                                file_id=str(meta.id), thread_id=str(body.thread_id)
                            )
                        except Exception:
                            redis = getattr(request.app.state, "redis", None)
                            if redis is not None:
                                await release(redis, "docquota:commit", claims.sub)
                            raise
                    else:
                        # Pinecone (no local staging concept — managed
                        # ingest, unchanged from before this feature). For a
                        # "local" backend this branch should be unreachable:
                        # the pre-validation pass above already 425/422'd
                        # any local-backend new commit whose staged_at
                        # wasn't set, before this loop ever runs. Kept as a
                        # defensive fallback, not a designed code path.
                        data = await ctx.file_store.download(meta.object_key)
                        await ctx.rag_backend.ingest(
                            data,
                            collection=str(body.thread_id),
                            metadata={
                                "filename": meta.original_name,
                                "content_type": meta.content_type,
                                # Lets a citation open this exact file later: the
                                # DB id for /files/{id}/download, and the
                                # thread-relative path /workspace/file expects
                                # (session_path falls back to original_name in
                                # capabilities/knowledge/citations.py when this
                                # is None — e.g. an "uploads/" scoped file with
                                # no thread session).
                                "file_id": str(meta.id),
                                "session_path": _session_relative_path(meta.object_key)
                                or meta.original_name,
                            },
                        )
                    meta.rag_ingested_at = datetime.now(timezone.utc)
                    needs_commit = True
                attachments.append(_attachment_dict(meta))
                continue

            # No RAG backend configured — fall back to the old inline-extract
            # path so uploads still work.
            if meta.extracted_text:
                text_parts.append(
                    f"[File: {meta.original_name}]\n{meta.extracted_text}"
                )
                attachments.append(_attachment_dict(meta))
                continue

            data = await ctx.file_store.download(meta.object_key)
            text, engine = await _extract_document_text(
                data, meta.original_name, meta.content_type
            )
            if text is not None:
                text_parts.append(f"[File: {meta.original_name}]\n{text}")
                meta.extracted_text = text
                meta.extracted_at = datetime.now(timezone.utc)
                meta.extraction_engine = engine
                needs_commit = True
                attachments.append(_attachment_dict(meta))
                continue
            # Extraction failed (corrupt file, no extraction service
            # configured, ...) — fall through to attachment metadata below
            # so the model at least knows the file exists.

        elif meta.content_type.startswith("image/"):
            data = await ctx.file_store.download(meta.object_key)
            image_inputs.append(_ImagePayload(data=data, media_type=meta.content_type))
            continue
        elif meta.content_type.startswith("text/"):
            data = await ctx.file_store.download(meta.object_key)
            text_parts.append(
                f"[File: {meta.original_name}]\n"
                + data.decode("utf-8", errors="replace")
            )
            continue

        attachments.append(_attachment_dict(meta))

    if needs_commit:
        await db.commit()

    return "\n\n".join(text_parts), image_inputs, attachments


__all__ = ["_get_agent_deps", "_build_file_context"]
