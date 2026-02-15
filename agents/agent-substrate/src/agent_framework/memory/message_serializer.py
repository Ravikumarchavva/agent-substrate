"""Serialize / deserialize BaseClientMessage subtypes to plain dicts.

Every message class already exposes ``to_dict()`` / ``from_dict()``.
This module adds a single top-level pair that dispatches on the ``type``
discriminator so callers never need to know the concrete class.

Security notes:
  - Only known message types are accepted during deserialization.
  - Unknown types raise ``ValueError`` — fail-closed by design.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Type

from agent_framework.messages.base_message import BaseClientMessage
from agent_framework.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from agent_framework.messages._types import deserialize_media_content

# ---------------------------------------------------------------------------
# Registry: type discriminator → concrete class
# ---------------------------------------------------------------------------

_MESSAGE_REGISTRY: Dict[str, Type[BaseClientMessage]] = {
    "SystemMessage": SystemMessage,
    "UserMessage": UserMessage,
    "AssistantMessage": AssistantMessage,
    "ToolCallMessage": ToolCallMessage,
    "ToolExecutionResultMessage": ToolExecutionResultMessage,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def serialize_message(message: BaseClientMessage) -> Dict[str, Any]:
    """Convert a ``BaseClientMessage`` subclass to a JSON-safe dict.

    The returned dict always contains a ``"type"`` key so that
    ``deserialize_message`` can reconstruct the correct class.
    """
    data = message.to_dict()
    # Guarantee the type discriminator is present
    if "type" not in data:
        data["type"] = type(message).__name__
    return data


def deserialize_message(data: Dict[str, Any]) -> BaseClientMessage:
    """Reconstruct a ``BaseClientMessage`` from a dict produced by
    ``serialize_message``.

    Handles deserialization of media content fields (content lists containing
    serialized dicts like ``{"type": "output_text", "text": "..."}``).

    Raises:
        ValueError: If the ``type`` key is missing or unknown.
    """
    msg_type = data.get("type")
    if not msg_type:
        raise ValueError("Message dict missing required 'type' discriminator")

    cls = _MESSAGE_REGISTRY.get(msg_type)
    if cls is None:
        raise ValueError(
            f"Unknown message type '{msg_type}'. "
            f"Allowed: {sorted(_MESSAGE_REGISTRY)}"
        )

    # Pre-process content for message types that use MediaType lists.
    # AssistantMessage and UserMessage serialize content as dicts (e.g.
    # {"type": "output_text", "text": "..."}) which need to be converted
    # back to native MediaType objects before Pydantic validation.
    if msg_type in ("AssistantMessage", "UserMessage"):
        raw_content = data.get("content")
        if isinstance(raw_content, list):
            deserialized_content = []
            for item in raw_content:
                if isinstance(item, dict):
                    deserialized_content.append(deserialize_media_content(item))
                else:
                    deserialized_content.append(item)
            data = {**data, "content": deserialized_content}

    return cls.from_dict(data)


def serialize_messages(messages: List[BaseClientMessage]) -> str:
    """Serialize a list of messages to a JSON string."""
    return json.dumps(
        [serialize_message(m) for m in messages],
        default=str,  # handles datetime, UUID, etc.
    )


def deserialize_messages(raw: str) -> List[BaseClientMessage]:
    """Deserialize a JSON string back to a list of messages.

    Raises:
        json.JSONDecodeError: If ``raw`` is not valid JSON.
        ValueError: If any message dict has an unknown type.
    """
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("Expected a JSON array of message dicts")
    return [deserialize_message(item) for item in items]
