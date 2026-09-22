"""Google Gemini API encoder.

Converts framework ``ChatMessage`` instances directly to Gemini's
Content / Part format without going through an OpenAI intermediate dict.

Public API::

    system_instruction, contents = encode_messages(messages)
    tools = encode_tools(tool_schemas)
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from PIL import Image

from google.genai import types as genai_types

from substrate.integrations.llm.encoders._media import pil_to_png_bytes

from substrate.kernel import ChatMessage
from substrate.kernel.core.content import (
    DataBlock,
    ErrorBlock,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# ── Content encoding helpers ─────────────────────────────────────────────────


def _encode_text(text: str) -> genai_types.Part:
    return genai_types.Part(text=text)


def _encode_image(img: Image.Image) -> genai_types.Part:
    """PIL Image → Gemini inline_data Part."""
    return genai_types.Part(
        inline_data=genai_types.Blob(mime_type="image/png", data=pil_to_png_bytes(img))
    )


def _encode_media_item(
    item: str
    | Image.Image
    | MediaBlock
    | TextBlock,
) -> genai_types.Part:
    """Encode a single block item to a Gemini Part."""
    from PIL import Image

    if isinstance(item, TextBlock):
        return _encode_text(item.text)
    if isinstance(item, Image.Image):
        return _encode_image(item)
    if isinstance(item, MediaBlock):
        if item.type == "image":
            if item.url:
                if item.url.startswith("data:"):
                    parts = item.url.split(",", 1)
                    media_type = (
                        parts[0].split(":")[1].split(";")[0]
                        if ":" in parts[0]
                        else "image/png"
                    )
                    data = base64.b64decode(parts[1]) if len(parts) > 1 else b""
                    return genai_types.Part(
                        inline_data=genai_types.Blob(mime_type=media_type, data=data)
                    )
                return genai_types.Part(
                    file_data=genai_types.FileData(
                        file_uri=item.url, mime_type="image/jpeg"
                    )
                )
            if item.file_id:
                return _encode_text(f"[Image file: {item.file_id}]")
            return genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type=item.media_type, data=item.data or b""
                )
            )
        if item.type in ("audio", "video"):
            return genai_types.Part(
                inline_data=genai_types.Blob(
                    mime_type=item.media_type, data=item.data or b""
                )
            )
        if item.type == "document":
            if item.data:
                return genai_types.Part(
                    inline_data=genai_types.Blob(
                        mime_type=item.media_type, data=item.data
                    )
                )
            if item.url:
                return genai_types.Part(
                    file_data=genai_types.FileData(
                        file_uri=item.url, mime_type=item.media_type
                    )
                )
            return _encode_text(f"[Document Attachment: {item.filename or 'document'}]")
    if isinstance(item, str):
        return _encode_text(item)
    raise ValueError(f"Unsupported content type: {type(item)}")


# ── Message-level encoding ───────────────────────────────────────────────────


def _encode_user(msg: ChatMessage) -> genai_types.Content:
    """User ChatMessage → Gemini Content with user role."""
    parts = []
    for item in msg.content:
        if isinstance(item, (MediaBlock, TextBlock)):
            parts.append(_encode_media_item(item))
    return genai_types.Content(role="user", parts=parts)


def _encode_assistant(msg: ChatMessage) -> genai_types.Content | None:
    """Assistant ChatMessage → Gemini Content with model role."""
    parts: list[genai_types.Part] = []

    for item in msg.content:
        if isinstance(item, TextBlock):
            if item.text.strip():
                parts.append(_encode_text(item.text))
        elif isinstance(item, ToolUseBlock):
            tc_args = item.arguments
            if isinstance(tc_args, str):
                tc_args = json.loads(tc_args)
            parts.append(
                genai_types.Part(
                    function_call=genai_types.FunctionCall(
                        name=item.tool_name, args=tc_args
                    )
                )
            )

    if parts:
        return genai_types.Content(role="model", parts=parts)
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


def _encode_tool_result(block: ToolResultBlock) -> genai_types.Content:
    """ToolResultBlock → Gemini function_response Content."""
    parts_text = []
    media_parts = []

    if block.content:
        for item in block.content:
            if isinstance(item, TextBlock):
                parts_text.append(item.text)
            elif isinstance(item, (DataBlock, ErrorBlock)):
                text = _get_block_text(item)
                if text:
                    parts_text.append(text)
            elif isinstance(item, MediaBlock):
                try:
                    media_parts.append(_encode_media_item(item))
                except Exception as e:
                    import logging

                    logging.getLogger(
                        "substrate.kernel.messages.encoders.gemini"
                    ).warning(
                        "Failed to encode media item for Gemini function response: %s",
                        e,
                    )

    content_str = "\n".join(parts_text)
    tool_name = block.name or "unknown_tool"

    parts = [
        genai_types.Part(
            function_response=genai_types.FunctionResponse(
                name=tool_name, response={"result": content_str}
            )
        )
    ]
    parts.extend(media_parts)

    return genai_types.Content(role="user", parts=parts)


# ── Public API ───────────────────────────────────────────────────────────────


def encode_messages(
    messages: list[ChatMessage],
) -> tuple[str, list[genai_types.Content]]:
    """Encode framework messages to Gemini GenerateContent format.

    Returns:
        system_instruction: Concatenated system prompt text.
        contents: List of Gemini Content objects.
    """
    system_parts: list[str] = []
    contents: list[genai_types.Content] = []

    for msg in messages:
        if msg.role == "system":
            system_parts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
        elif msg.role == "user":
            contents.append(_encode_user(msg))
        elif msg.role == "assistant":
            encoded = _encode_assistant(msg)
            if encoded:
                contents.append(encoded)
        elif msg.role == "tool":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    contents.append(_encode_tool_result(block))

    return "\n".join(system_parts).strip(), contents


def encode_tools(
    tools: list[dict[str, Any]] | None,
    convert_schema: Any = None,
) -> list[genai_types.Tool] | None:
    """Convert tool schemas to Gemini format.

    Args:
        tools: List of tool schemas in any supported format.
        convert_schema: Optional callable to convert JSON Schema → Gemini schema.
            If None, schemas are passed through as-is.

    Returns ``None`` when *tools* is falsy.
    """
    if not tools:
        return None

    declarations: list[genai_types.FunctionDeclaration] = []
    for tool in tools:
        name = ""
        description = ""
        parameters: dict[str, Any] = {"type": "OBJECT", "properties": {}}

        # Flattened Responses API format
        if "type" in tool and "name" in tool and "parameters" in tool:
            name = tool["name"]
            description = tool.get("description", "")
            raw_params = tool["parameters"]
        # OpenAI nested format
        elif tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
            name = fn.get("name", "")
            description = fn.get("description", "")
            raw_params = fn.get("parameters", {"type": "object", "properties": {}})
        # MCP format
        elif "name" in tool and "inputSchema" in tool:
            name = tool["name"]
            description = tool.get("description", "")
            raw_params = tool["inputSchema"]
        # Generic named tool
        elif "name" in tool:
            name = tool["name"]
            description = tool.get("description", "")
            raw_params = (
                tool.get("parameters")
                or tool.get("inputSchema")
                or {"type": "object", "properties": {}}
            )
        else:
            continue

        parameters = convert_schema(raw_params) if convert_schema else raw_params

        if name:
            declarations.append(
                genai_types.FunctionDeclaration(
                    name=name,
                    description=description,
                    parameters=cast(Any, parameters),
                )
            )

    return (
        [genai_types.Tool(function_declarations=declarations)] if declarations else None
    )
