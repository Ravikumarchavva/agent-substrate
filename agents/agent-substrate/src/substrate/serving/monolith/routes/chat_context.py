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
        elif meta.content_type.startswith("text/"):
            text_parts.append(
                f"[File: {meta.original_name}]\n"
                + data.decode("utf-8", errors="replace")
            )
        else:
            attachments.append(
                {
                    "id": str(meta.id),
                    "name": meta.original_name,
                    "mime": meta.content_type,
                    "size": meta.size_bytes,
                }
            )

    return "\n\n".join(text_parts), image_inputs, attachments


__all__ = ["_get_agent_deps", "_build_file_context"]
