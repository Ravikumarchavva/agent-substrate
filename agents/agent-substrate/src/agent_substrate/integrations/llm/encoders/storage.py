"""Neutral storage encoder for persistence (Redis / Postgres).

Serialises framework messages to a provider-agnostic JSON format that
round-trips cleanly through ``serialize_message`` / ``deserialize_message``.

This replaces the old ``message_serializer.py`` logic that was coupled to
OpenAI Responses API format.

Public API::

    data = serialize_message(msg)      # ChatMessage → dict
    msg  = deserialize_message(data)   # dict → ChatMessage

    json_str = serialize_messages(msgs)    # list → JSON string
    msgs     = deserialize_messages(s)     # JSON string → list
"""

from __future__ import annotations

import base64
import json
from typing import Any

from agent_substrate.kernel import ChatMessage


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


def serialize_message(message: ChatMessage) -> dict[str, Any]:
    """Serialize a single message to a dict suitable for JSON storage."""
    # Use model_dump() instead of model_dump(mode="json") to preserve bytes
    raw_data = message.model_dump()
    data = _bytes_to_b64(raw_data)
    return data


def deserialize_message(data: dict[str, Any]) -> ChatMessage:
    """Deserialize a dict to a ChatMessage."""
    clean_data = _b64_to_bytes(data)
    return ChatMessage.model_validate(clean_data)


def serialize_messages(messages: list[ChatMessage]) -> str:
    """Serialize a list of messages to a JSON string."""
    return json.dumps(
        [serialize_message(msg) for msg in messages],
        default=str,
    )


def deserialize_messages(data: str) -> list[ChatMessage]:
    """Deserialize a JSON string to a list of messages."""
    items = json.loads(data)
    return [deserialize_message(item) for item in items]
