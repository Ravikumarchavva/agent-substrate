"""Backward-compatible wrappers around the neutral storage message encoder.

This module preserves the historical import path used by the memory backends
while delegating all serialization logic to
``raavan.core.messages.encoders.storage``.
"""

from __future__ import annotations

from typing import Any

from raavan.core.messages.base_message import BaseClientMessage
from raavan.core.messages.encoders.storage import (
    deserialize_message as _deserialize_message,
    deserialize_messages as _deserialize_messages,
    serialize_message as _serialize_message,
    serialize_messages as _serialize_messages,
)


def serialize_message(message: BaseClientMessage) -> dict[str, Any]:
    """Convert a ``BaseClientMessage`` subclass to a JSON-safe dict."""
    return _serialize_message(message)


def deserialize_message(data: dict[str, Any]) -> BaseClientMessage:
    """Reconstruct a ``BaseClientMessage`` from a storage dict."""
    return _deserialize_message(data)


def serialize_messages(messages: list[BaseClientMessage]) -> str:
    """Serialize a list of messages to a JSON string."""
    return _serialize_messages(messages)


def deserialize_messages(raw: str) -> list[BaseClientMessage]:
    """Deserialize a JSON string back to a list of messages."""
    return _deserialize_messages(raw)
