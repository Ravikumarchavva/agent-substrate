"""Anthropic Messages API encoder.

Converts framework ``BaseClientMessage`` instances directly to Anthropic's
Messages API format without going through an OpenAI intermediate dict.

Public API::

    system, messages = encode_messages(messages)
    tools = encode_tools(tool_schemas)
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from raavan.core.messages._types import (
    AudioContent,
    ImageContent,
    VideoContent,
)
from raavan.core.messages.base_message import BaseClientMessage
from raavan.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolExecutionResultMessage,
    UserMessage,
)


# ── Content encoding helpers ─────────────────────────────────────────────────


def _encode_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _encode_image(img: Image.Image) -> dict[str, Any]:
    """PIL Image → Anthropic base64 image block."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": b64},
    }


def _encode_image_content(ic: ImageContent) -> dict[str, Any]:
    """ImageContent → Anthropic image block."""
    if ic.url:
        return {"type": "image", "source": {"type": "url", "url": ic.url}}
    if ic.file_id:
        # Anthropic doesn't support file_id natively — use URL fallback
        return {"type": "text", "text": f"[Image file: {ic.file_id}]"}
    # Raw bytes
    b64 = base64.b64encode(ic.data or b"").decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": ic.media_type, "data": b64},
    }


def _encode_media_item(
    item: str | Image.Image | ImageContent | AudioContent | VideoContent,
) -> dict[str, Any]:
    """Encode a single MediaType item to Anthropic content block."""
    if isinstance(item, Image.Image):
        return _encode_image(item)
    if isinstance(item, ImageContent):
        return _encode_image_content(item)
    if isinstance(item, AudioContent):
        # Anthropic doesn't support audio natively — text fallback
        return _encode_text("[Audio content]")
    if isinstance(item, VideoContent):
        # Anthropic doesn't support video natively — text fallback
        return _encode_text("[Video content]")
    if isinstance(item, str):
        return _encode_text(item)
    raise ValueError(f"Unsupported content type: {type(item)}")


# ── Message-level encoding ───────────────────────────────────────────────────


def _encode_user(msg: UserMessage) -> dict[str, Any]:
    """UserMessage → Anthropic user message."""
    content = [_encode_media_item(item) for item in msg.content]
    return {"role": "user", "content": content}


def _encode_assistant(msg: AssistantMessage) -> dict[str, Any] | None:
    """AssistantMessage → Anthropic assistant message with tool_use blocks."""
    blocks: list[dict[str, Any]] = []

    if msg.content:
        for item in msg.content:
            if isinstance(item, str) and item.strip():
                blocks.append(_encode_text(item))

    if msg.tool_calls:
        for tc in msg.tool_calls:
            tc_args = tc.arguments
            if isinstance(tc_args, str):
                tc_args = json.loads(tc_args)
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc_args,
                }
            )

    if blocks:
        return {"role": "assistant", "content": blocks}
    return None


def _encode_tool_result(msg: ToolExecutionResultMessage) -> dict[str, Any]:
    """ToolExecutionResultMessage → Anthropic tool_result in user message."""
    content_str = ""
    if msg.content:
        if isinstance(msg.content, list):
            content_str = "\n".join(
                block.get("text", "")
                for block in msg.content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        elif isinstance(msg.content, str):
            content_str = msg.content  # type: ignore[assignment]

    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": content_str,
            }
        ],
    }


# ── Public API ───────────────────────────────────────────────────────────────


def encode_messages(
    messages: list[BaseClientMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Encode framework messages to Anthropic Messages API format.

    Returns:
        system: Concatenated system prompt text.
        conversation: List of Anthropic message items.
    """
    system_parts: list[str] = []
    conversation: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_parts.append(msg.content)
        elif isinstance(msg, UserMessage):
            conversation.append(_encode_user(msg))
        elif isinstance(msg, AssistantMessage):
            encoded = _encode_assistant(msg)
            if encoded:
                conversation.append(encoded)
        elif isinstance(msg, ToolExecutionResultMessage):
            conversation.append(_encode_tool_result(msg))

    return "\n".join(system_parts).strip(), conversation


def encode_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert tool schemas to Anthropic format.

    Returns ``None`` when *tools* is falsy.
    """
    if not tools:
        return None

    result: list[dict[str, Any]] = []
    for tool in tools:
        # Flattened Responses API format
        if "type" in tool and "name" in tool and "parameters" in tool:
            result.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["parameters"],
                }
            )
        # OpenAI nested format
        elif tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
            result.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        # MCP format
        elif "name" in tool and "inputSchema" in tool:
            result.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["inputSchema"],
                }
            )
        # Generic named tool
        elif "name" in tool:
            result.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": (
                        tool.get("parameters")
                        or tool.get("inputSchema")
                        or {"type": "object", "properties": {}}
                    ),
                }
            )
        else:
            result.append(tool)

    return result
