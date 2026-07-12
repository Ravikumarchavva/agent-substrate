"""OpenAI Responses API message encoder.

Converts framework ``ChatMessage`` instances directly to the
OpenAI Chat Completions API format without going through an intermediate dict.

Public API::

    instructions, input_items = encode_messages(messages)
    tools = encode_tools(tool_schemas)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from substrate.integrations.llm.encoders._media import (
    bytes_to_base64,
    pil_to_base64_png,
)

from substrate.kernel import ChatMessage
from substrate.kernel.core.content import (
    AudioBlock,
    CodeBlock,
    DataBlock,
    DocumentBlock,
    ErrorBlock,
    ImageBlock,
    TextBlock,
    VideoBlock,
    ToolUseBlock,
    ToolResultBlock,
)


def _make_optional_schema_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert an optional property schema to a required-but-nullable schema."""
    nullable = dict(schema)
    schema_type = nullable.get("type")
    if isinstance(schema_type, str) and schema_type != "null":
        nullable["type"] = [schema_type, "null"]
        return nullable
    if isinstance(schema_type, list) and "null" not in schema_type:
        nullable["type"] = [*schema_type, "null"]
        return nullable

    for key in ("anyOf", "oneOf"):
        options = nullable.get(key)
        if isinstance(options, list) and not any(
            isinstance(option, dict) and option.get("type") == "null"
            for option in options
        ):
            nullable[key] = [*options, {"type": "null"}]
            return nullable

    return nullable


def ensure_strict_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool schemas to OpenAI strict function-calling rules.

    OpenAI strict mode requires:
    1. ``additionalProperties: false`` on every object node.
    2. Every property to appear in ``required``.
    3. Previously-optional properties to become nullable.
    """
    normalized = dict(schema)

    if normalized.get("type") == "object":
        normalized["additionalProperties"] = False
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            existing_required = set(normalized.get("required", []))
            strict_properties: dict[str, Any] = {}
            for key, value in properties.items():
                property_schema = (
                    ensure_strict_tool_schema(value)
                    if isinstance(value, dict)
                    else value
                )
                if key not in existing_required and isinstance(property_schema, dict):
                    property_schema = _make_optional_schema_nullable(property_schema)
                strict_properties[key] = property_schema
            normalized["properties"] = strict_properties
            normalized["required"] = list(properties.keys())

    for key in ("items", "additionalProperties", "not"):
        value = normalized.get(key)
        if isinstance(value, dict):
            normalized[key] = ensure_strict_tool_schema(value)

    for key in ("properties", "$defs", "definitions"):
        value = normalized.get(key)
        if isinstance(value, dict):
            normalized[key] = {
                item_key: ensure_strict_tool_schema(item_value)
                if isinstance(item_value, dict)
                else item_value
                for item_key, item_value in value.items()
            }

    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        value = normalized.get(key)
        if isinstance(value, list):
            normalized[key] = [
                ensure_strict_tool_schema(item) if isinstance(item, dict) else item
                for item in value
            ]

    return normalized


# ── Content encoding helpers ─────────────────────────────────────────────────


def _encode_image(img: Image.Image) -> dict[str, Any]:
    """PIL Image → OpenAI Responses API ``input_image`` block."""
    return {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{pil_to_base64_png(img)}",
    }


def _encode_image_content(ic: ImageBlock) -> dict[str, Any]:
    """ImageBlock → OpenAI Responses API ``input_image`` block."""
    block: dict[str, Any] = {"type": "input_image"}
    if ic.url:
        block["image_url"] = ic.url
    elif ic.file_id:
        block["file_id"] = ic.file_id
    else:
        block["image_url"] = (
            f"data:{ic.media_type};base64,{bytes_to_base64(ic.data or b'')}"
        )
    if ic.detail != "auto":
        block["detail"] = ic.detail
    return block


def _encode_audio_content(ac: AudioBlock, role: str) -> dict[str, Any]:
    """AudioBlock → OpenAI Responses API audio block."""
    if isinstance(ac.data, (str, Path)):
        with open(ac.data, "rb") as f:
            audio_bytes = f.read()
    else:
        audio_bytes = ac.data
    audio_type = "input_audio" if role == "user" else "output_audio"
    return {
        "type": audio_type,
        "source": {
            "type": "base64",
            "media_type": ac.media_type,
            "data": bytes_to_base64(audio_bytes or b""),
        },
    }


def _encode_video_content(vc: VideoBlock, role: str) -> dict[str, Any]:
    """VideoBlock → OpenAI Responses API video block."""
    if isinstance(vc.data, (str, Path)):
        with open(vc.data, "rb") as f:
            video_bytes = f.read()
    else:
        video_bytes = vc.data
    if video_bytes is None:
        raise ValueError("VideoBlock requires data bytes to encode for OpenAI")
    video_type = "input_video" if role == "user" else "output_video"
    return {
        "type": video_type,
        "source": {
            "type": "base64",
            "media_type": vc.media_type,
            "data": bytes_to_base64(video_bytes),
        },
    }


def _encode_media_item(
    item: str | Image.Image | ImageBlock | AudioBlock | VideoBlock | DocumentBlock,
    role: str,
) -> dict[str, Any]:
    """Encode a single media block to OpenAI Responses API format."""
    if isinstance(item, Image.Image):
        return _encode_image(item)
    if isinstance(item, ImageBlock):
        return _encode_image_content(item)
    if isinstance(item, AudioBlock):
        return _encode_audio_content(item, role)
    if isinstance(item, VideoBlock):
        return _encode_video_content(item, role)
    if isinstance(item, DocumentBlock):
        text_type = "input_text" if role == "user" else "output_text"
        ref = item.filename or item.url or "document"
        return {"type": text_type, "text": f"[Document Attachment: {ref}]"}
    if isinstance(item, str):
        text_type = "input_text" if role == "user" else "output_text"
        return {"type": text_type, "text": item}
    raise ValueError(f"Unsupported content type: {type(item)}")


# ── Message-level encoding ───────────────────────────────────────────────────


def _encode_system(msg: ChatMessage, parts: list[str]) -> None:
    """Append system message text to instructions parts."""
    for block in msg.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)


def _encode_user(msg: ChatMessage) -> dict[str, Any]:
    """User ChatMessage → Responses API message item."""
    content = []
    for block in msg.content:
        if isinstance(block, (ImageBlock, AudioBlock, VideoBlock, DocumentBlock)):
            content.append(_encode_media_item(block, "user"))
        elif isinstance(block, TextBlock):
            content.append({"type": "input_text", "text": block.text})
    return {"type": "message", "role": "user", "content": content}


def _encode_assistant(msg: ChatMessage, items: list[dict[str, Any]]) -> None:
    """Assistant ChatMessage → Responses API message + function_call items."""
    content = []
    for block in msg.content:
        if isinstance(block, (ImageBlock, AudioBlock, VideoBlock, DocumentBlock)):
            content.append(_encode_media_item(block, "assistant"))
        elif isinstance(block, TextBlock):
            content.append({"type": "output_text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            tc_args = block.arguments
            if isinstance(tc_args, dict):
                tc_args = json.dumps(tc_args)
            items.append(
                {
                    "type": "function_call",
                    "call_id": block.call_id,
                    "name": block.tool_name,
                    "arguments": tc_args,
                }
            )
    if content:
        items.append({"type": "message", "role": "assistant", "content": content})


def _encode_content_block(block: Any) -> str:
    """Encode a single ContentBlock to a string for OpenAI tool output."""
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ImageBlock):
        return f"[Image: {block.media_type}]"
    if isinstance(block, AudioBlock):
        if block.transcript:
            return f"[Audio transcript]: {block.transcript}"
        return f"[Audio: {block.media_type}]"
    if isinstance(block, VideoBlock):
        ref = block.url or block.media_type
        return f"[Video: {ref}]"
    if isinstance(block, DocumentBlock):
        name = block.filename or block.media_type
        return f"[Document: {name}]"
    if isinstance(block, DataBlock):
        return json.dumps(block.data)
    if isinstance(block, CodeBlock):
        return f"```{block.language}\n{block.code}\n```"
    if isinstance(block, ErrorBlock):
        return f"[{block.error_type}]: {block.message}"
    # Fallback for unknown block types or legacy dicts
    max_len = 100
    if isinstance(block, dict):
        if "text" in block:
            text = block["text"]
            if isinstance(text, str):
                return text if len(text) <= max_len else text[:max_len] + "..."
        try:
            return json.dumps(block)[:max_len] + "..."
        except Exception:
            return "[Unserializable content]"
    return str(block)[:max_len] + "..."


def _encode_tool_result(block: ToolResultBlock) -> list[dict[str, Any]]:
    content_str = ""
    if block.content:
        parts = [_encode_content_block(b) for b in block.content]
        content_str = "\n".join(p for p in parts if p)

    items: list[dict[str, Any]] = [
        {
            "type": "function_call_output",
            "call_id": block.call_id,
            "output": content_str,
        }
    ]
    # Handle media attached to tool results if they exist (requires inspecting contents)
    media_blocks = [
        b
        for b in block.content
        if isinstance(b, (ImageBlock, AudioBlock, VideoBlock, DocumentBlock))
    ]
    if media_blocks:
        media_content = [
            {
                "type": "input_text",
                "text": (
                    "Tool-generated artifact(s) for the previous step. "
                    "Use these attachments if they are relevant."
                ),
            }
        ]
        media_content.extend(_encode_media_item(item, "user") for item in media_blocks)
        items.append(
            {
                "type": "message",
                "role": "user",
                "content": media_content,
            }
        )
    return items


# ── Public API ───────────────────────────────────────────────────────────────


def encode_messages(
    messages: list[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Encode framework messages to OpenAI Responses API format.

    Returns:
        instructions: Concatenated system prompt text.
        conversation_input: List of Responses API input items.
    """
    instruction_parts: list[str] = []
    conversation_input: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            _encode_system(msg, instruction_parts)
        elif msg.role == "user":
            conversation_input.append(_encode_user(msg))
        elif msg.role == "assistant":
            _encode_assistant(msg, conversation_input)
        elif msg.role == "tool":
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    conversation_input.extend(_encode_tool_result(block))

    # OpenAI 400: every function_call must have a matching function_call_output.
    # If the history was persisted partially (crash / timeout mid-run) there may
    # be orphaned function_calls. Add synthetic outputs so the API doesn't reject.
    call_ids: set[str] = set()
    output_ids: set[str] = set()
    for item in conversation_input:
        t = item.get("type")
        if t == "function_call":
            call_ids.add(item.get("call_id", ""))
        elif t == "function_call_output":
            output_ids.add(item.get("call_id", ""))
    for orphan_id in call_ids - output_ids:
        if orphan_id:
            conversation_input.append(
                {
                    "type": "function_call_output",
                    "call_id": orphan_id,
                    "output": "[Tool output not available]",
                }
            )

    return "\n".join(instruction_parts).strip(), conversation_input


def encode_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Normalise tool schemas to OpenAI Responses API format.

    Accepts:
    - Standard named-tool dicts (framework format)
    - OpenAI nested Chat-Completions format  ``{"type":"function","function":{...}}``
    - MCP format  ``{"name":..., "inputSchema":...}``
    - ``{"type":"tool_search"}``  or  ``{"type":"tool_search","execution":"client"}``
    - ``{"type":"namespace", "name":..., "tools":[...]}``  — passed through as-is

    Returns ``None`` when *tools* is falsy.
    """
    if not tools:
        return None

    def _flatten_tool(
        tool_name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool_name,
            "description": description,
            "parameters": ensure_strict_tool_schema(parameters),
            "strict": True,
        }

    result: list[dict[str, Any]] = []
    for tool in tools:
        tool_type = tool.get("type")

        # ── Special OpenAI types — pass through unchanged ─────────────────
        # tool_search sentinel  {"type": "tool_search"}  or  {..., "execution": "client"}
        # namespace             {"type": "namespace", "name":..., "tools":[...]}
        if tool_type in ("tool_search", "namespace"):
            result.append(tool)
            continue

        # Already flattened Responses-API format
        if tool_type in ("function",) and "name" in tool and "parameters" in tool:
            result.append(
                _flatten_tool(
                    tool_name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                )
            )
        # Named tool without explicit type (framework format)
        elif "name" in tool and "parameters" in tool:
            result.append(
                _flatten_tool(
                    tool_name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool["parameters"],
                )
            )
        # OpenAI nested Chat-Completions format  {"type":"function","function":{...}}
        elif tool_type == "function" and "function" in tool:
            fn = tool["function"]
            result.append(
                _flatten_tool(
                    tool_name=fn.get("name", ""),
                    description=fn.get("description", ""),
                    parameters=fn.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                )
            )
        # MCP format  {"name":..., "inputSchema":...}
        elif "name" in tool and "inputSchema" in tool:
            result.append(
                _flatten_tool(
                    tool_name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool["inputSchema"],
                )
            )
        # Generic named tool — best-effort
        elif "name" in tool:
            result.append(
                _flatten_tool(
                    tool_name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=(
                        tool.get("parameters")
                        or tool.get("inputSchema")
                        or {"type": "object", "properties": {}}
                    ),
                )
            )
        else:
            result.append(tool)

    return result
