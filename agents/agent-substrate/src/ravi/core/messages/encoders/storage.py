"""Neutral storage encoder for persistence (Redis / Postgres).

Serialises framework messages to a provider-agnostic JSON format that
round-trips cleanly through ``serialize_message`` / ``deserialize_message``.

This replaces the old ``message_serializer.py`` logic that was coupled to
OpenAI Responses API format.

Public API::

    data = serialize_message(msg)      # BaseClientMessage → dict
    msg  = deserialize_message(data)   # dict → BaseClientMessage

    json_str = serialize_messages(msgs)    # list → JSON string
    msgs     = deserialize_messages(s)     # JSON string → list
"""

from __future__ import annotations

import base64
import json
from typing import Any

from ravi.core.messages.base_message import BaseClientMessage
from ravi.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)

# ── Type → class mapping ────────────────────────────────────────────────────

_MESSAGE_CLASSES: dict[str, type[BaseClientMessage]] = {
    "SystemMessage": SystemMessage,
    "UserMessage": UserMessage,
    "AssistantMessage": AssistantMessage,
    "ToolCallMessage": ToolCallMessage,
    "ToolExecutionResultMessage": ToolExecutionResultMessage,
}


# ── Public API ───────────────────────────────────────────────────────────────


def _bytes_to_b64(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: _bytes_to_b64(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_bytes_to_b64(x) for x in val]
    elif isinstance(val, bytes):
        return {"__bytes_b64__": base64.b64encode(val).decode("utf-8")}
    return val


def _b64_to_bytes(val: Any) -> Any:
    if isinstance(val, dict):
        if "__bytes_b64__" in val:
            return base64.b64decode(val["__bytes_b64__"])
        return {k: _b64_to_bytes(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_b64_to_bytes(x) for x in val]
    return val


def serialize_message(message: BaseClientMessage) -> dict[str, Any]:
    """Serialize a single message to a dict suitable for JSON storage."""
    # Use model_dump() instead of model_dump(mode="json") to preserve bytes
    raw_data = message.model_dump()
    data = _bytes_to_b64(raw_data)
    # Ensure discriminator is always present
    if "type" not in data:
        data["type"] = type(message).__name__
    return data


def deserialize_message(data: dict[str, Any]) -> BaseClientMessage:
    """Deserialize a dict to the correct message subclass."""
    msg_type = data.get("type", "")
    cls = _MESSAGE_CLASSES.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown message type: {msg_type!r}")

    clean_data = _b64_to_bytes(data)
    return cls.model_validate(clean_data)


def serialize_messages(messages: list[BaseClientMessage]) -> str:
    """Serialize a list of messages to a JSON string."""
    return json.dumps(
        [serialize_message(msg) for msg in messages],
        default=str,
    )


def deserialize_messages(data: str) -> list[BaseClientMessage]:
    """Deserialize a JSON string to a list of messages."""
    items = json.loads(data)
    return [deserialize_message(item) for item in items]
