"""OpenAI Responses API message encoder.

Converts framework ``BaseClientMessage`` instances directly to the
OpenAI Responses API format without going through an intermediate dict.

Public API::

    instructions, input_items = encode_messages(messages)
    tools = encode_tools(tool_schemas)
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from ravi.core.messages._types import (
    AudioContent,
    ImageContent,
    VideoContent,
)
from ravi.core.messages.base_message import BaseClientMessage
from ravi.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
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
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"}


def _encode_image_content(ic: ImageContent) -> dict[str, Any]:
    """ImageContent → OpenAI Responses API ``input_image`` block."""
    block: dict[str, Any] = {"type": "input_image"}
    if ic.url:
        block["image_url"] = ic.url
    elif ic.file_id:
        block["file_id"] = ic.file_id
    else:
        b64 = base64.b64encode(ic.data or b"").decode("utf-8")
        block["image_url"] = f"data:{ic.media_type};base64,{b64}"
    if ic.detail != "auto":
        block["detail"] = ic.detail
    return block


def _encode_audio_content(ac: AudioContent, role: str) -> dict[str, Any]:
    """AudioContent → OpenAI Responses API audio block."""
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
            "media_type": f"audio/{ac.format}",
            "data": base64.b64encode(audio_bytes).decode("utf-8"),
        },
    }


def _encode_video_content(vc: VideoContent, role: str) -> dict[str, Any]:
    """VideoContent → OpenAI Responses API video block."""
    if isinstance(vc.data, (str, Path)):
        with open(vc.data, "rb") as f:
            video_bytes = f.read()
    else:
        video_bytes = vc.data
    video_type = "input_video" if role == "user" else "output_video"
    return {
        "type": video_type,
        "source": {
            "type": "base64",
            "media_type": f"video/{vc.format}",
            "data": base64.b64encode(video_bytes).decode("utf-8"),
        },
    }


def _encode_media_item(
    item: str | Image.Image | ImageContent | AudioContent | VideoContent, role: str
) -> dict[str, Any]:
    """Encode a single MediaType item to OpenAI Responses API format."""
    if isinstance(item, Image.Image):
        return _encode_image(item)
    if isinstance(item, ImageContent):
        return _encode_image_content(item)
    if isinstance(item, AudioContent):
        return _encode_audio_content(item, role)
    if isinstance(item, VideoContent):
        return _encode_video_content(item, role)
    if isinstance(item, str):
        text_type = "input_text" if role == "user" else "output_text"
        return {"type": text_type, "text": item}
    raise ValueError(f"Unsupported content type: {type(item)}")


# ── Message-level encoding ───────────────────────────────────────────────────


def _encode_system(msg: SystemMessage, parts: list[str]) -> None:
    """Append system message text to instructions parts."""
    parts.append(msg.content)


def _encode_user(msg: UserMessage) -> dict[str, Any]:
    """UserMessage → Responses API message item."""
    content = [_encode_media_item(item, "user") for item in msg.content]
    return {"type": "message", "role": "user", "content": content}


def _encode_assistant(msg: AssistantMessage, items: list[dict[str, Any]]) -> None:
    """AssistantMessage → Responses API message + function_call items."""
    if msg.content:
        serialized = [_encode_media_item(item, "assistant") for item in msg.content]
        if serialized:
            items.append(
                {"type": "message", "role": "assistant", "content": serialized}
            )

    if msg.tool_calls:
        for tc in msg.tool_calls:
            if not (hasattr(tc, "name") and hasattr(tc, "arguments")):
                continue
            tc_args = tc.arguments
            if isinstance(tc_args, dict):
                tc_args = json.dumps(tc_args)
            items.append(
                {
                    "type": "function_call",
                    "call_id": tc.id,
                    "name": tc.name,
                    "arguments": tc_args,
                }
            )


def _encode_tool_result(msg: ToolExecutionResultMessage) -> dict[str, Any]:
    """ToolExecutionResultMessage → Responses API function_call_output item."""
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
        "type": "function_call_output",
        "call_id": msg.tool_call_id,
        "output": content_str,
    }


# ── Public API ───────────────────────────────────────────────────────────────


def encode_messages(
    messages: list[BaseClientMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Encode framework messages to OpenAI Responses API format.

    Returns:
        instructions: Concatenated system prompt text.
        conversation_input: List of Responses API input items.
    """
    instruction_parts: list[str] = []
    conversation_input: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            _encode_system(msg, instruction_parts)
        elif isinstance(msg, UserMessage):
            conversation_input.append(_encode_user(msg))
        elif isinstance(msg, AssistantMessage):
            _encode_assistant(msg, conversation_input)
        elif isinstance(msg, ToolExecutionResultMessage):
            conversation_input.append(_encode_tool_result(msg))
        elif isinstance(msg, ToolCallMessage):
            # Standalone tool call (rare — usually embedded in AssistantMessage)
            tc_args = msg.arguments
            if isinstance(tc_args, dict):
                tc_args = json.dumps(tc_args)
            conversation_input.append(
                {
                    "type": "function_call",
                    "call_id": msg.id,
                    "name": msg.name,
                    "arguments": tc_args,
                }
            )

    return "\n".join(instruction_parts).strip(), conversation_input


def encode_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Normalise tool schemas to OpenAI Responses API flattened format.

    Accepts OpenAI nested format, MCP format, or already-flattened format.
    Returns ``None`` when *tools* is falsy.
    """
    if not tools:
        return None

    def _flatten_tool(
        tool_name: str, description: str, parameters: dict[str, Any]
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
        # Already flattened Responses-API format
        if "type" in tool and "name" in tool and "parameters" in tool:
            result.append(
                _flatten_tool(
                    tool_name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                )
            )
        # OpenAI nested Chat Completions format
        elif tool.get("type") == "function" and "function" in tool:
            fn = tool["function"]
            result.append(
                _flatten_tool(
                    tool_name=fn.get("name"),
                    description=fn.get("description", ""),
                    parameters=fn.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                )
            )
        # MCP format with inputSchema
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
