"""Chat per-request dependency + file-context assembly.

Split out of ``chat.py``.
"""

from __future__ import annotations

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


async def _extract_pdf_text(data: bytes, name: str) -> str | None:
    """Best-effort PDF text extraction (pypdf/pdfplumber) for inlining into
    the prompt. Returns ``None`` on any failure or an empty/scanned PDF —
    the caller falls back to metadata-only attachment handling in that case.
    """
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
    if not text:
        return None

    max_chars = settings.ATTACHMENT_PDF_MAX_CHARS
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[...truncated to {max_chars} characters]"
    return text


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

    for meta in rows:
        data = await ctx.file_store.download(meta.object_key)
        if meta.content_type.startswith("image/"):
            image_inputs.append(_ImagePayload(data=data, media_type=meta.content_type))
            continue
        elif meta.content_type.startswith("text/"):
            text_parts.append(
                f"[File: {meta.original_name}]\n"
                + data.decode("utf-8", errors="replace")
            )
            continue
        elif meta.content_type == "application/pdf":
            pdf_text = await _extract_pdf_text(data, meta.original_name)
            if pdf_text is not None:
                text_parts.append(f"[File: {meta.original_name}]\n{pdf_text}")
                continue
            # Extraction failed (scanned/image-only PDF, corrupt file, or
            # pypdf/pdfplumber unavailable) — fall through to attachment
            # metadata below so the model at least knows the file exists.

        attachment: dict[str, Any] = {
            "id": str(meta.id),
            "name": meta.original_name,
            "mime": meta.content_type,
            "size": meta.size_bytes,
        }
        # object_key is "users/{uid}/sessions/{tid}/name" under the
        # WorkspaceFileStore default — that's exactly the path the
        # code interpreter's sandbox sees at /app/workspace (which
        # mounts users/{uid}), minus the "users/{uid}/" prefix. Give
        # the model that relative path so generated code can open the
        # file directly instead of only knowing its display name.
        parts = meta.object_key.split("/", 2)
        if len(parts) == 3 and parts[0] == "users":
            attachment["workspace_path"] = parts[2]
        attachments.append(attachment)

    return "\n\n".join(text_parts), image_inputs, attachments


__all__ = ["_get_agent_deps", "_build_file_context"]
