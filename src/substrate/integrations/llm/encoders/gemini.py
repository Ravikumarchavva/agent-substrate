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
from typing import Any, cast

from google.genai import types as genai_types

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


def _encode_media_item(item: MediaBlock | TextBlock) -> genai_types.Part:
    """Encode a single block item to a Gemini Part."""
    if isinstance(item, TextBlock):
        return _encode_text(item.text)
    if item.type == "image":
        if item.url:
            if item.url.startswith("data:"):
                head, _, payload = item.url.partition(",")
                media_type = head[5:].split(";")[0] or item.media_type
                return genai_types.Part(
                    inline_data=genai_types.Blob(
                        mime_type=media_type, data=base64.b64decode(payload)
                    )
                )
            return genai_types.Part(
                file_data=genai_types.FileData(
                    file_uri=item.url, mime_type=item.media_type
                )
            )
        if item.file_id:
            return _encode_text(f"[Image file not sent: {item.file_id}]")
        return genai_types.Part(
            inline_data=genai_types.Blob(mime_type=item.media_type, data=item.data or b"")
        )
    if item.type in ("audio", "video", "document"):
        if item.data:
            return genai_types.Part(
                inline_data=genai_types.Blob(mime_type=item.media_type, data=item.data)
            )
        if item.url:
            return genai_types.Part(
                file_data=genai_types.FileData(
                    file_uri=item.url, mime_type=item.media_type
                )
            )
        return _encode_text(f"[{item.type} not sent: {item.filename or item.media_type}]")
    return _encode_text(f"[{item.type} not sent]")


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


def _response_part(item: MediaBlock) -> genai_types.FunctionResponsePart | None:
    """A media block as a native part of a function response (Gemini 3+)."""
    if item.data:
        return genai_types.FunctionResponsePart(
            inline_data=genai_types.FunctionResponseBlob(
                mime_type=item.media_type, data=item.data, display_name=item.filename
            )
        )
    if item.url:
        return genai_types.FunctionResponsePart(
            file_data=genai_types.FileData(file_uri=item.url, mime_type=item.media_type)
        )
    return None


def _encode_tool_results(
    blocks: list[ToolResultBlock], *, native_media: bool
) -> genai_types.Content:
    """All tool results of one turn → ONE user Content.

    Gemini requires the responses to a turn's function calls to arrive
    together, one ``function_response`` part per call, in call order.

    Media a tool returned goes inside its ``function_response`` when
    ``native_media`` (Gemini 3+ multimodal function responses); older models
    don't accept that, so the media follows as ordinary parts of the same
    turn, after all the function responses.
    """
    response_parts: list[genai_types.Part] = []
    trailing_media: list[genai_types.Part] = []

    for block in blocks:
        texts: list[str] = []
        native: list[genai_types.FunctionResponsePart] = []
        for item in block.content:
            if isinstance(item, TextBlock):
                texts.append(item.text)
            elif isinstance(item, (DataBlock, ErrorBlock)):
                text = _get_block_text(item)
                if text:
                    texts.append(text)
            elif isinstance(item, MediaBlock):
                part = _response_part(item) if native_media else None
                if part is not None:
                    native.append(part)
                else:
                    trailing_media.append(_encode_media_item(item))

        text = "\n".join(texts)
        # "output"/"error" are the keys Gemini documents for a function response.
        payload = {"error": text} if block.is_error else {"output": text}
        response_parts.append(
            genai_types.Part(
                function_response=genai_types.FunctionResponse(
                    name=block.name or "unknown_tool",
                    response=payload,
                    parts=native or None,
                )
            )
        )

    return genai_types.Content(role="user", parts=[*response_parts, *trailing_media])


# ── Public API ───────────────────────────────────────────────────────────────


def encode_messages(
    messages: list[ChatMessage], *, native_tool_media: bool = False
) -> tuple[str, list[genai_types.Content]]:
    """Encode framework messages to Gemini GenerateContent format.

    ``native_tool_media``: put tool-returned media inside the function
    response (Gemini 3+) instead of as trailing parts of the same turn.

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
            results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            if results:
                contents.append(
                    _encode_tool_results(results, native_media=native_tool_media)
                )

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
