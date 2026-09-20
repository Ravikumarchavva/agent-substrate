"""Chat per-request dependency + file-context assembly.

Split out of ``chat.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.infrastructure.serving_factory import build_chat_tools
from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.runtimes.document_intelligence.extract import extract_document
from substrate.runtimes.document_intelligence.service.convert import (
    CONVERTIBLE_CONTENT_TYPES,
)
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


# Absolute workspace mount path inside sandboxes
_SANDBOX_WORKSPACE_MOUNT_PATH = "/app/workspace"

# Types eligible for upload-time size caps and eager RAG staging
EXTRACTABLE_CONTENT_TYPES = (
    {"application/pdf", "text/markdown"} | CONVERTIBLE_CONTENT_TYPES
)


def _session_relative_path(object_key: str) -> str | None:
    """``tenants/{tid}/users/{uid}/conversations/{cid}/branches/{bid}/workspace/shared/{rest}``
    → ``rest``, or ``None`` if *object_key* isn't a conversation-scoped
    upload (e.g. a bare user-scoped ``tenants/{tid}/users/{uid}/artifacts/...``
    key with no ``conversations/`` segment).

    Must stay in step with ``agents/workspace/layout.py`` —
    ``conversation_shared_key`` builds exactly the keys parsed here, and
    ``code_interpreter/tool.py`` mounts that same ``.../workspace/shared``
    prefix at the sandbox's ``/workspace``, so ``rest`` is the path the
    sandbox sees.

    Shared by the nsjail workspace-path branch of ``_attachment_dict``
    below and by the RAG-ingest metadata: both need the path a citation's
    "open this file" click uses (``routes/workspace.py::serve_file``),
    relative to the conversation's shared dir — never ``original_name``,
    which can differ from the real object-key basename when
    ``_unique_object_key`` (``routes/files.py``) appended a uniquifying
    suffix.
    """
    parts = object_key.split("/", 10)
    if (
        len(parts) == 11
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
        and parts[6] == "branches"
        and parts[8] == "workspace"
        and parts[9] == "shared"
    ):
        return parts[10]
    return None


def _truncate(text: str) -> str:
    max_chars = settings.ATTACHMENT_PDF_MAX_CHARS
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[...truncated to {max_chars} characters]"
    return text


async def _build_file_context(
    db: AsyncSession,
    body: ChatRequest,
    request: Request,
    ctx: ServerDependencies,
    claims: AuthClaims,
) -> tuple[str, list[_ImagePayload], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve file context for the chat turn.

    Two different things need two different file sets, which is why this
    returns both:

    * ``attachments`` (3rd) — everything the *model* should know about:
      newly attached files (``body.file_ids``, sent only on the turn the
      composer had them staged) plus every previously uploaded file still on
      this thread. Without the latter, a file was only visible to the model
      on the single turn it was attached — the frontend composer clears its
      local attachment list right after send (substrate-ui's
      ``doSendMessage``), so a follow-up like "summarize that" carried no
      ``file_ids`` and the model had no way to know the file existed.
    * ``new_attachments`` (4th) — only ``body.file_ids``, for the *this
      message's* displayed attachment cards. This one must stay narrow: it
      gets stamped onto the persisted user-message log entry
      (``routes/chat.py``'s ``metadata["attachments"]``) and rendered back
      as that one message's attachment cards on every later page load. Using
      the broad `attachments` list there instead — an earlier version of
      this fix did exactly that — made every message in a thread display
      every file ever uploaded to it, duplicated across every turn.
    """
    if ctx.file_store is None:
        return "", [], [], []

    from sqlalchemy import or_, select

    from substrate.serving.monolith.models import FileMetadata

    conditions = [FileMetadata.thread_id == body.thread_id]
    if body.file_ids:
        conditions.append(FileMetadata.id.in_(body.file_ids))

    rows = (
        (
            await db.execute(
                select(FileMetadata).where(
                    or_(*conditions),
                    FileMetadata.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return "", [], [], []

    new_file_ids = {str(fid) for fid in (body.file_ids or [])}

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
        # Resolve absolute path inside the sandbox based on active mount topology
        relative_path: str | None = None
        mount_path = _SANDBOX_WORKSPACE_MOUNT_PATH

        # Extractable types (e.g. PDF) are indexed into RAG, so workspace_path
        # (the only field attachments_block/chat_intents.py actually puts in
        # front of the model) is omitted for them — that's what steers the
        # model toward knowledge_search/session_document_search instead of
        # reading the raw file via code_interpreter. session_path below is a
        # UI-only field the model never sees (opens the file in the read-only
        # side-panel viewer — substrate-ui's AttachmentDocumentCard), so it
        # must still be set regardless of content type: this early-skip used
        # to fall all the way through past it too, silently downgrading every
        # extractable file's attachment card to an external-tab download link
        # instead of the in-panel viewer.
        if meta.content_type not in EXTRACTABLE_CONTENT_TYPES:
            if settings.SANDBOX_RUNTIME == "nsjail":
                mount_path = "/workspace"
                relative_path = _session_relative_path(meta.object_key)
            elif settings.CI_WORKSPACE_PVC_CLAIM:
                # The k8s pod's PVC subPath is `tenants/{tid}/users/{uid}`
                # (sandbox_service.py::_ensure_user_template), so a path inside
                # the pod is whatever follows that prefix in the object key —
                # `conversations/{cid}/branches/{bid}/workspace/shared/{name}`
                # for a real conversation file. Anchored at fixed positions,
                # matching every other new-shape parser in this codebase (see
                # routes/workspace.py's _is_version_key/_session_id_from_key).
                parts = meta.object_key.split("/")
                if len(parts) >= 5 and parts[0] == "tenants" and parts[2] == "users":
                    relative_path = "/".join(parts[4:])
            if relative_path is not None:
                attachment["workspace_path"] = f"{mount_path}/{relative_path}"

        # Workspace-relative path (no sandbox mount prefix, no dependency
        # on which SANDBOX_RUNTIME happens to be configured) — lets the UI
        # open this exact file in the same read-only artifact viewer a
        # `sandbox:` link uses (buildWorkspaceFileUrl expects a path
        # relative to the conversation's shared workspace root).
        attachment["session_path"] = _session_relative_path(meta.object_key)
        return attachment

    for meta in rows:
        if meta.promoted_at is None:
            # Attachments live only in the local pending store (see
            # capabilities/storage/pending.py) until the message
            # referencing them is actually sent — this is that moment.
            # Must happen before any of the ctx.file_store.download(...)
            # calls below, for both extractable and non-extractable
            # (workspace-mounted) files.
            from substrate.serving.monolith.routes.files import promote_pending_file

            await promote_pending_file(ctx, meta)
            needs_commit = True
        if meta.content_type in EXTRACTABLE_CONTENT_TYPES:
            # Extractable docs are ingested into the user's per-user
            # session-document index instead of inlined into the prompt —
            # the agent retrieves relevant passages via
            # session_document_search. Cache hit: already ingested (files
            # are immutable once uploaded), skip re-ingesting on every
            # later reference.
            if ctx.rag_backend is not None:
                if meta.rag_ingested_at is None:
                    already_staged_for_this_thread = (
                        ctx.rag_backend.name == "local"
                        and meta.staged_at is not None
                        and meta.thread_id is not None
                        and str(meta.thread_id) == str(body.thread_id)
                    )
                    if already_staged_for_this_thread:
                        # Already extracted+embedded+indexed at upload time
                        # (routes/files.py::_stage_uploaded_doc), already
                        # tagged with this exact session_id — nothing to
                        # move. The pre-validation pass above already
                        # confirmed staging succeeded and quota was
                        # consumed for this file before we got here.
                        pass
                    elif ctx.rag_backend.name == "local" and ctx.embedding_client is not None:
                        # Not staged (thread_id wasn't known at upload time)
                        # or referenced from a different thread than it was
                        # uploaded under — either way, index it now, tagged
                        # with *this* message's real thread_id.
                        from substrate.capabilities.knowledge.session_ingest import (
                            ingest_session_document,
                        )

                        data = await ctx.file_store.download(meta.object_key)
                        try:
                            await ingest_session_document(
                                data=data,
                                filename=meta.original_name,
                                content_type=meta.content_type,
                                tenant_id=claims.tenant_id,
                                user_id=claims.sub,
                                session_id=str(body.thread_id),
                                cfg=settings,
                                embedding_client=ctx.embedding_client,
                                model_client=ctx.model_client,
                                rag_backend=ctx.rag_backend,
                            )
                        except Exception:
                            redis = getattr(request.app.state, "redis", None)
                            if redis is not None:
                                await release(redis, "docquota:commit", claims.sub)
                            raise
                    else:
                        # A non-local backend (no eager-staging concept --
                        # managed ingest at send time instead). For the
                        # "local" backend (the only one today) this branch
                        # should be unreachable: the pre-validation pass
                        # above already 425/422'd any local-backend new
                        # commit whose staged_at wasn't set, before this
                        # loop ever runs. Kept as a defensive fallback, not
                        # a designed code path.
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
            endpoint = None
            if settings.DOCUMENT_INTELLIGENCE_SERVICE_URL:
                endpoint = InferenceEndpoint(
                    model="",
                    base_url=settings.DOCUMENT_INTELLIGENCE_SERVICE_URL,
                    api_key=settings.DOCUMENT_INTELLIGENCE_AUTH_TOKEN,
                    timeout_s=settings.DOCUMENT_INTELLIGENCE_TIMEOUT_S,
                )
            result = await extract_document(
                data, meta.original_name, meta.content_type, endpoint=endpoint
            )
            text = result.markdown.strip() or None
            if text is not None:
                text = _truncate(text)
                text_parts.append(f"[File: {meta.original_name}]\n{text}")
                meta.extracted_text = text
                meta.extracted_at = datetime.now(timezone.utc)
                meta.extraction_engine = result.engine
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

    new_attachments = [a for a in attachments if a.get("id") in new_file_ids]
    return "\n\n".join(text_parts), image_inputs, attachments, new_attachments


__all__ = ["_get_agent_deps", "_build_file_context"]
