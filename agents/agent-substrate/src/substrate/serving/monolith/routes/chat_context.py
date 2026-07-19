"""Chat per-request dependency + file-context assembly.

Split out of ``chat.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.infrastructure.serving_factory import build_chat_tools
from substrate.serving.monolith.dependencies import ServerDependencies
from substrate.serving.monolith.schemas import ChatRequest
from substrate.serving.monolith.routes.chat_wire import _ImagePayload
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

# Docling (when DOCLING_SERVICE_URL is configured) can read these too;
# pypdf/pdfplumber cannot, so without docling they stay metadata-only.
_DOCLING_ONLY_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
}
_EXTRACTABLE_TYPES = {"application/pdf", *_DOCLING_ONLY_TYPES}


def _truncate(text: str) -> str:
    max_chars = settings.ATTACHMENT_PDF_MAX_CHARS
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[...truncated to {max_chars} characters]"
    return text


async def _extract_via_pypdf(data: bytes, name: str) -> str | None:
    """pypdf/pdfplumber fallback — PDF only, no docling dependency."""
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

    Tries the docling service first (structure-aware: tables, layout, OCR,
    DOCX/PPTX support) when ``DOCLING_SERVICE_URL`` is configured. Falls
    back to the lightweight local pypdf/pdfplumber path for PDFs on any
    docling failure/timeout/non-configuration — DOCX/PPTX have no local
    fallback (pypdf can't read them), so those just return ``(None, None)``
    when docling isn't available or fails.
    """
    if settings.DOCLING_SERVICE_URL:
        from substrate.capabilities.knowledge.docling_client import DoclingClient

        client = DoclingClient(
            base_url=settings.DOCLING_SERVICE_URL,
            auth_token=settings.DOCLING_AUTH_TOKEN,
            timeout_s=settings.DOCLING_TIMEOUT_S,
        )
        try:
            result = await client.extract(data, name, content_type)
        finally:
            await client.close()
        if result.success and result.text.strip():
            return _truncate(result.text.strip()), "docling"
        logger.info(
            "Docling extraction unavailable/failed for %r (%s); falling back",
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
        # these two backends use different mount topologies (mirrors the
        # ci_has_workspace_access gate below) — and the result is always
        # made absolute (see _SANDBOX_WORKSPACE_MOUNT_PATH) since the
        # sandbox's execution cwd isn't guaranteed to be the workspace root.
        relative_path: str | None = None
        if settings.CI_WORKSPACE_PVC_CLAIM:
            # K8s agent-sandbox subPath-mounts "users/{uid}" at
            # /app/workspace (per-user pod — that subPath IS the isolation
            # boundary), so the prefix must be stripped.
            parts = meta.object_key.split("/", 2)
            if len(parts) == 3 and parts[0] == "users":
                relative_path = parts[2]
        elif settings.CI_LOCAL_SANDBOX_URL:
            # Local sandbox is one shared container (not per-user pods) with
            # the whole workspace root bind-mounted unscoped at
            # /app/workspace — object_key is already the right relative path.
            relative_path = meta.object_key
        if relative_path is not None:
            attachment["workspace_path"] = (
                f"{_SANDBOX_WORKSPACE_MOUNT_PATH}/{relative_path}"
            )
        return attachment

    for meta in rows:
        if meta.content_type in _EXTRACTABLE_TYPES:
            # Cache hit — this exact file was already extracted (by this
            # thread or any other; files are immutable once uploaded, so
            # the cached text never goes stale). Skip both the download
            # and the extraction call entirely.
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
            # Extraction failed (scanned/image-only PDF, corrupt file, no
            # docling service for DOCX/PPTX, ...) — fall through to
            # attachment metadata below so the model at least knows the
            # file exists.

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
