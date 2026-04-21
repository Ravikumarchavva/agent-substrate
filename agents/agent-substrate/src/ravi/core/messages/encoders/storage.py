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

import json
from typing import Any

from ravi.core.messages._types import deserialize_media_content
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


def serialize_message(message: BaseClientMessage) -> dict[str, Any]:
    """Serialize a single message to a dict suitable for JSON storage."""
    data = message.to_dict()
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

    # Pre-process content for types that need media deserialization
    if msg_type in ("UserMessage", "AssistantMessage"):
        content = data.get("content")
        if isinstance(content, list):
            data = {
                **data,
                "content": [deserialize_media_content(item) for item in content],
            }

    return cls.from_dict(data)


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
