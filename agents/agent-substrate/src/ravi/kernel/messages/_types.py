"""Stream chunk types and content re-exports.

Re-exports all content types from ``content.py`` as the single source of truth,
and defines the streaming chunk hierarchy for user-facing visibility streams.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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

MediaType = MessageContent  # backward-compatible alias


class StreamChunk(BaseModel):
    """Base class for streaming chunks from LLM/Agent."""

    chunk_type: str
    metadata: JsonObject = Field(default_factory=dict)


class TextDeltaChunk(StreamChunk):
    """Incremental text content."""

    chunk_type: Literal["text_delta"] = "text_delta"  # type: ignore[assignment]
    text: str


class ReasoningDeltaChunk(StreamChunk):
    """Incremental reasoning/thinking content."""

    chunk_type: Literal["reasoning_delta"] = "reasoning_delta"  # type: ignore[assignment]
    text: str


class CompletionChunk(StreamChunk):
    """Final completion event with full response."""

    chunk_type: Literal["completion"] = "completion"  # type: ignore[assignment]
    message: AssistantMessage

    model_config = {"arbitrary_types_allowed": True}


__all__ = [
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
    "MediaType",
    "StreamChunk",
    "TextDeltaChunk",
    "ReasoningDeltaChunk",
    "CompletionChunk",
]
