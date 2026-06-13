"""Anthropic Messages API encoder.

Converts framework ``ChatMessage`` instances directly to Anthropic's
Messages API format without going through an OpenAI intermediate dict.

Public API::

    system, messages = encode_messages(messages)
    tools = encode_tools(tool_schemas)
"""

from __future__ import annotations

import json
from typing import Any

from PIL import Image

from ravi.integrations.llm.encoders._media import bytes_to_base64, pil_to_base64_png

from ravi.kernel import ChatMessage
from ravi.kernel.core.content import (
    AudioBlock,
    ImageBlock,
    VideoBlock,
    DocumentBlock,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ErrorBlock,
    DataBlock,
    CodeBlock,
)


# ── Content encoding helpers ─────────────────────────────────────────────────


def _encode_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _encode_image(img: Image.Image) -> dict[str, Any]:
    """PIL Image → Anthropic base64 image block."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": pil_to_base64_png(img),
        },
    }


def _encode_image_content(ic: ImageBlock) -> dict[str, Any]:
    """ImageBlock → Anthropic image block."""
    if ic.url:
        return {"type": "image", "source": {"type": "url", "url": ic.url}}
    if ic.file_id:
        # Anthropic doesn't support file_id natively — use URL fallback
        return {"type": "text", "text": f"[Image file: {ic.file_id}]"}
    # Raw bytes
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": ic.media_type,
            "data": bytes_to_base64(ic.data or b""),
        },
    }


def _encode_media_item(
    item: str
    | Image.Image
    | ImageBlock
    | AudioBlock
    | VideoBlock
    | DocumentBlock
    | TextBlock,
) -> dict[str, Any]:
    """Encode a single block to Anthropic content block."""
    if isinstance(item, TextBlock):
        return _encode_text(item.text)
    if isinstance(item, Image.Image):
        return _encode_image(item)
    if isinstance(item, ImageBlock):
        return _encode_image_content(item)
    if isinstance(item, AudioBlock):
        # Anthropic doesn't support audio natively — text fallback
        return _encode_text("[Audio content]")
    if isinstance(item, VideoBlock):
        # Anthropic doesn't support video natively — text fallback
        return _encode_text("[Video content]")
    if isinstance(item, DocumentBlock):
        # Anthropic supports PDF documents natively!
        if item.media_type == "application/pdf" and item.data:
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": bytes_to_base64(item.data),
                },
            }
        ref = item.filename or item.url or "document"
        return _encode_text(f"[Document Attachment: {ref}]")
    if isinstance(item, str):
        return _encode_text(item)
    raise ValueError(f"Unsupported content type: {type(item)}")


# ── Message-level encoding ───────────────────────────────────────────────────


def _encode_user(msg: ChatMessage) -> dict[str, Any]:
    """User ChatMessage → Anthropic user message."""
    content = []
    for item in msg.content:
        if isinstance(
            item, (ImageBlock, AudioBlock, VideoBlock, DocumentBlock, TextBlock)
        ):
            content.append(_encode_media_item(item))
    return {"role": "user", "content": content}


def _encode_assistant(msg: ChatMessage) -> dict[str, Any] | None:
    """Assistant ChatMessage → Anthropic assistant message with tool_use blocks."""
    blocks: list[dict[str, Any]] = []

    for item in msg.content:
        if isinstance(item, TextBlock):
            if item.text.strip():
                blocks.append(_encode_text(item.text))
        elif isinstance(item, ToolUseBlock):
            tc_args = item.arguments
            if isinstance(tc_args, str):
                tc_args = json.loads(tc_args)
            blocks.append(
                {
                    "type": "tool_use",
                    "id": item.call_id,
                    "name": item.tool_name,
                    "input": tc_args,
                }
            )

    if blocks:
        return {"role": "assistant", "content": blocks}
    return None


def _get_block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if hasattr(block, "type"):
        if block.type == "text" and hasattr(block, "text"):
            return block.text
        if block.type == "code" and hasattr(block, "code"):
            lang = getattr(block, "language", "python")
            return f"```{lang}\n{block.code}\n```"
        if block.type == "data" and hasattr(block, "data"):
            try:
                return json.dumps(block.data)
            except Exception:
                return str(block.data)
        if block.type == "error":
            err_type = getattr(block, "error_type", "Error")
            msg = getattr(block, "message", "")
            return f"[{err_type}]: {msg}"
        if block.type == "document" and hasattr(block, "data"):
            filename = getattr(block, "filename", None) or "document"
            media_type = getattr(block, "media_type", "application/octet-stream")
            return f"[Document: {filename} ({media_type})]"
        return ""
    if isinstance(block, dict):
        b_type = block.get("type", "text")
        if b_type == "text":
            return str(block.get("text", ""))
        if b_type == "code":
            lang = block.get("language", "python")
            return f"```{lang}\n{block.get('code', '')}\n```"
        if b_type == "data":
            try:
                return json.dumps(block.get("data", {}))
            except Exception:
                return str(block.get("data", ""))
        if b_type == "error":
            return f"[{block.get('error_type', 'Error')}]: {block.get('message', '')}"
        if b_type == "document":
            filename = block.get("filename") or "document"
            media_type = block.get("media_type", "application/octet-stream")
            return f"[Document: {filename} ({media_type})]"
    return ""


def _encode_tool_result(block: ToolResultBlock) -> dict[str, Any]:
    """ToolResultBlock → Anthropic tool_result in user message."""
    content_blocks: list[dict[str, Any]] = []

    # Add text and media content from content blocks
    if block.content:
        for item in block.content:
            if isinstance(item, TextBlock):
                content_blocks.append(_encode_text(item.text))
            elif isinstance(item, (ImageBlock, AudioBlock, VideoBlock, DocumentBlock)):
                try:
                    content_blocks.append(_encode_media_item(item))
                except Exception as e:
                    import logging

                    logging.getLogger(
                        "ravi.kernel.messages.encoders.anthropic"
                    ).warning(
                        "Failed to encode media item for Anthropic tool result: %s", e
                    )
            elif isinstance(item, (DataBlock, CodeBlock, ErrorBlock)):
                text = _get_block_text(item)
                if text:
                    content_blocks.append(_encode_text(text))

    # Fallback to empty string if no content blocks were generated
    if not content_blocks:
        content_blocks.append(_encode_text(""))

    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": block.call_id,
                "content": content_blocks,
            }
        ],
    }


# ── Public API ───────────────────────────────────────────────────────────────


def encode_messages(
    messages: list[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Encode framework messages to Anthropic Messages API format.

    Returns:
        system: Concatenated system prompt text.
        conversation: List of Anthropic message items.
    """
    system_parts: list[str] = []
    conversation: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_parts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
        elif msg.role == "user":
            conversation.append(_encode_user(msg))
        elif msg.role == "assistant":
            encoded = _encode_assistant(msg)
            if encoded:
                conversation.append(encoded)
        elif msg.role == "tool":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    conversation.append(_encode_tool_result(block))

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
