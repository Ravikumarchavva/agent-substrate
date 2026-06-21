"""Agent service — persistence helpers for chat threads.

Agent *construction* moved to ``agent_substrate.infrastructure.serving_factory``.
This module only contains database persistence helpers that have no
dependency on agents or capabilities.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agent_substrate.kernel import ChatMessage, TextBlock, ToolUseBlock
from agent_substrate.logger import setup_logging

from agent_substrate.serving.monolith.services import (
    create_step,
    load_messages_for_memory,
)

logger = setup_logging()

__all__ = [
    "persist_user_message",
    "persist_assistant_message",
    "persist_tool_result",
    "load_messages_for_memory",
]


async def persist_user_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    content: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> uuid.UUID:
    """Save a user message step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="user_message",
        name="user",
        input=content,
        metadata=metadata,
    )
    return step.id


async def persist_assistant_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    message: ChatMessage | Any,
    *,
    parent_id: Optional[uuid.UUID] = None,
    tool_meta_map: Optional[Dict[str, Dict]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> uuid.UUID:
    """Save an assistant message step and return its ID."""
    generation: Dict[str, Any] = {
        "finish_reason": getattr(message, "finish_reason", "stop"),
    }
    usage = getattr(message, "usage", None)
    if usage:
        generation["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

    tool_calls = getattr(message, "tool_calls", None)
    if (
        not tool_calls
        and hasattr(message, "content")
        and isinstance(message.content, list)
    ):
        tool_calls = [
            block for block in message.content if isinstance(block, ToolUseBlock)
        ]

    if tool_calls:
        serialized_tcs = []
        for tc in tool_calls:
            tc_name = getattr(tc, "name", getattr(tc, "tool_name", "unknown"))
            tc_data = tc.model_dump(mode="json") if hasattr(tc, "model_dump") else {}
            if tool_meta_map and tc_name in tool_meta_map:
                meta = tool_meta_map[tc_name]
                ui_info = meta.get("ui", {})
                resource_uri = ui_info.get("resourceUri", "")
                if resource_uri:
                    from agent_substrate.serving.monolith.routes.mcp_apps import resolve_ui_uri

                    http_url = resolve_ui_uri(resource_uri) or resource_uri
                    tc_data["_meta"] = {
                        "ui": {"resourceUri": resource_uri, "httpUrl": http_url}
                    }
            serialized_tcs.append(tc_data)
        generation["tool_calls"] = serialized_tcs

    output_text = None
    if hasattr(message, "content") and message.content:
        texts = []
        for c in message.content:
            if isinstance(c, TextBlock):
                texts.append(c.text)
            elif isinstance(c, str):
                texts.append(c)
        output_text = "\n".join(texts) if texts else None

    step = await create_step(
        db,
        thread_id=thread_id,
        type="assistant_message",
        name="assistant",
        output=output_text,
        generation=generation,
        parent_id=parent_id,
        metadata=metadata,
    )
    return step.id


async def persist_tool_result(
    db: AsyncSession,
    thread_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    output: str,
    is_error: bool = False,
    *,
    parent_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Save a tool result step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="tool_result",
        name=tool_name,
        output=output,
        is_error=is_error,
        metadata={"tool_call_id": tool_call_id},
        parent_id=parent_id,
    )
    return step.id
