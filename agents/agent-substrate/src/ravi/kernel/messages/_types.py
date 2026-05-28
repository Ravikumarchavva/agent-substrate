"""Stream chunk types and backward-compatible re-exports.

This module re-exports all content types from ``content.py`` and defines
the streaming chunk hierarchy used by ``generate_stream()``.

Stream chunks are Pydantic models with a ``chunk_type`` discriminator so
they serialize cleanly over SSE / WebSocket boundaries.

.. note::
    ``MediaType`` is kept as a backward-compatible alias for
    ``MessageContent``.  New code should use ``MessageContent`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

# Re-export all content types — single source of truth is content.py
from ravi.kernel.messages.content import (
    AudioBlock,
    AudioContent,
    CodeBlock,
    ContentBlock,
    DataBlock,
    DocumentBlock,
    DocumentContent,
    ErrorBlock,
    ImageBlock,
    ImageContent,
    JsonObject,
    JsonPrimitive,
    JsonValue,
    MediaContent,
    MessageContent,
    ResourceBlock,
    TextBlock,
    VideoBlock,
    VideoContent,
    content_block_from_dict,
    content_blocks_to_str,
)

from ravi.kernel.messages.client_messages import AssistantMessage

if TYPE_CHECKING:
    from ravi.kernel.structured.result import StructuredOutputResult

# Backward-compatible alias — new code should use MessageContent
MediaType = MessageContent


# ── Stream chunk hierarchy (Pydantic, typed, serializable) ───────────────


class StreamChunk(BaseModel):
    """Base class for streaming chunks from LLM/Agent.

    Every chunk carries a ``chunk_type`` discriminator so consumers can
    pattern-match without ``isinstance`` when working with serialized data.
    """

    chunk_type: str
    metadata: JsonObject = Field(default_factory=dict)


class TextDeltaChunk(StreamChunk):
    """Incremental text content."""

    chunk_type: Literal["text_delta"] = "text_delta"  # type: ignore[assignment]
    text: str


class ReasoningDeltaChunk(StreamChunk):
    """Incremental reasoning/thinking content (for o1/o3 models)."""

    chunk_type: Literal["reasoning_delta"] = "reasoning_delta"  # type: ignore[assignment]
    text: str


class CompletionChunk(StreamChunk):
    """Final completion event with full response.

    ``message`` is always an ``AssistantMessage`` instance.
    """

    chunk_type: Literal["completion"] = "completion"  # type: ignore[assignment]
    message: "AssistantMessage"

    model_config = {"arbitrary_types_allowed": True}


class StructuredOutputChunk(StreamChunk):
    """Yielded at the end of a streaming run when ``response_schema`` is set.

    Contains the validated result alongside the raw JSON text.
    Consumers can check ``chunk.result.ok`` before accessing
    ``chunk.result.parsed``.
    """

    chunk_type: Literal["structured_output"] = "structured_output"  # type: ignore[assignment]
    result: "StructuredOutputResult"  # type: ignore[type-arg]

    model_config = {"arbitrary_types_allowed": True}


__all__ = [
    # Content types (from content.py)
    "TextBlock",
    "ImageBlock",
    "AudioBlock",
    "VideoBlock",
    "DocumentBlock",
    "DocumentContent",
    "DataBlock",
    "CodeBlock",
    "ErrorBlock",
    "ResourceBlock",
    "ContentBlock",
    "ImageContent",
    "AudioContent",
    "VideoContent",
    "MediaContent",
    "MessageContent",
    "JsonPrimitive",
    "JsonValue",
    "JsonObject",
    "content_block_from_dict",
    "content_blocks_to_str",
    # Backward compat
    "MediaType",
    # Stream chunks
    "StreamChunk",
    "TextDeltaChunk",
    "ReasoningDeltaChunk",
    "CompletionChunk",
    "StructuredOutputChunk",
]
