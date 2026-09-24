"""OpenAI Responses API message encoder.

Converts framework ``ChatMessage`` instances directly to the
OpenAI Chat Completions API format without going through an intermediate dict.

Public API::

    instructions, input_items = encode_messages(messages)
    tools = encode_tools(tool_schemas)
"""

from __future__ import annotations

import json
from typing import Any

from substrate.integrations.llm.encoders._media import bytes_to_base64

from substrate.kernel import ChatMessage
from substrate.kernel.core.content import (
    DataBlock,
    ErrorBlock,
    MediaBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
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
#
# What the Responses API can carry, and where:
#   input_text / input_image / input_file — in user messages AND natively in
#       a function_call_output's ``output`` list (so a tool's image reaches
#       the model as part of the tool result itself);
#   input_audio — user messages only;
#   video — nowhere.
# Anything it can't carry becomes a short text note, never silence.


def _data_uri(item: MediaBlock) -> str:
    return f"data:{item.media_type};base64,{bytes_to_base64(item.data or b'')}"


def _note(item: MediaBlock) -> dict[str, Any]:
    ref = item.filename or item.url or item.file_id or item.media_type
    return {"type": "input_text", "text": f"[{item.type} not sent: {ref}]"}


def _encode_image(item: MediaBlock) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "input_image"}
    if item.file_id:
        block["file_id"] = item.file_id
    else:
        block["image_url"] = item.url or _data_uri(item)
    if item.detail != "auto":
        block["detail"] = item.detail
    return block


def _encode_file(item: MediaBlock) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "input_file"}
    if item.file_id:
        block["file_id"] = item.file_id
    elif item.url:
        block["file_url"] = item.url
    else:
        block["file_data"] = _data_uri(item)
        block["filename"] = item.filename or "document"
    return block


def _encode_audio(item: MediaBlock) -> dict[str, Any]:
    fmt = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/mpeg": "mp3", "audio/mp3": "mp3"}.get(
        item.media_type
    )
    if item.data is None or fmt is None:
        return _note(item)
    return {
        "type": "input_audio",
        "input_audio": {"data": bytes_to_base64(item.data), "format": fmt},
    }


def _encode_media_item(item: MediaBlock, *, in_tool_output: bool = False) -> dict[str, Any]:
    if item.type == "image":
        return _encode_image(item)
    if item.type == "document":
        return _encode_file(item)
    if item.type == "audio" and not in_tool_output:
        return _encode_audio(item)
    return _note(item)


# ── Message-level encoding ───────────────────────────────────────────────────


def _encode_system(msg: ChatMessage, parts: list[str]) -> None:
    """Append system message text to instructions parts."""
    for block in msg.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)


def _encode_user(msg: ChatMessage) -> dict[str, Any]:
    """User ChatMessage → Responses API message item."""
    content: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, MediaBlock):
            content.append(_encode_media_item(block))
        elif isinstance(block, TextBlock):
            content.append({"type": "input_text", "text": block.text})
        elif isinstance(block, DataBlock):
            content.append({"type": "input_text", "text": json.dumps(block.data)})
    return {"type": "message", "role": "user", "content": content}


def _encode_assistant(msg: ChatMessage, items: list[dict[str, Any]]) -> None:
    """Assistant ChatMessage → Responses API message + function_call items."""
    content: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, TextBlock):
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


def _encode_tool_result(block: ToolResultBlock) -> list[dict[str, Any]]:
    """A tool result → ``function_call_output``.

    Text-only results go as a plain string. If the tool returned images or
    documents, ``output`` is a list of ``input_text``/``input_image``/
    ``input_file`` parts so the model sees them as part of the tool result.
    Audio can't ride in a tool output, so it follows in a user message.
    """
    parts: list[dict[str, Any]] = []
    follow_up: list[dict[str, Any]] = []
    has_media = False
    for b in block.content:
        if isinstance(b, TextBlock):
            if b.text:
                parts.append({"type": "input_text", "text": b.text})
        elif isinstance(b, DataBlock):
            parts.append({"type": "input_text", "text": json.dumps(b.data)})
        elif isinstance(b, ErrorBlock):
            parts.append({"type": "input_text", "text": f"[{b.error_type}]: {b.message}"})
        elif isinstance(b, MediaBlock):
            if b.type == "audio" and b.data is not None:
                follow_up.append(_encode_media_item(b))
                parts.append({"type": "input_text", "text": "[audio attached in the next message]"})
            else:
                parts.append(_encode_media_item(b, in_tool_output=True))
                has_media = True

    if block.is_error and parts and parts[0]["type"] == "input_text":
        parts[0] = {"type": "input_text", "text": f"Error: {parts[0]['text']}"}

    output: str | list[dict[str, Any]]
    if has_media:
        output = parts
    else:
        output = "\n".join(p["text"] for p in parts)

    items: list[dict[str, Any]] = [
        {"type": "function_call_output", "call_id": block.call_id, "output": output}
    ]
    if follow_up:
        items.append({"type": "message", "role": "user", "content": follow_up})
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
