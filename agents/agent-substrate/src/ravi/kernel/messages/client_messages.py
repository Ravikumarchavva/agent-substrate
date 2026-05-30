"""Concrete client message types for LLM API communication.

All messages are fully serializable Pydantic models.  Provider-specific
encoding (OpenAI, Anthropic, Gemini wire formats) lives in
``integrations.llm.encoders.<provider>`` — messages themselves are
provider-agnostic data containers.

Serialization: ``msg.model_dump(mode="json")`` for storage / wire.
Deserialization: ``SystemMessage.model_validate(data)`` etc.
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional
from uuid import uuid4

from pydantic import (
    BaseModel as PydanticBaseModel,
    ConfigDict,
    field_validator,
    Field,
)

from ravi.kernel.messages.base_message import BaseClientMessage, CLIENT_ROLES, UsageStats
from ravi.kernel.messages.content import (
    ContentBlock,
    ImageContent,
    AudioContent,
    VideoContent,
    DocumentContent,
    JsonObject,
    MediaContent,
    MessageContent,
    TextBlock,
    ImageBlock,
    content_block_from_dict,
    CONTENT_BLOCK_TYPES,
)


class SystemMessage(BaseClientMessage[str]):
    """System message for agent instructions."""

    role: CLIENT_ROLES = "system"
    content: str
    type: Literal["SystemMessage"] = "SystemMessage"  # type: ignore[override]


class UserMessage(BaseClientMessage[List[MessageContent]]):
    """User message with text or multimodal content."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "user"
    content: List[MessageContent]
    name: Optional[str] = None
    type: Literal["UserMessage"] = "UserMessage"  # type: ignore[override]

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, v: object) -> List[MessageContent]:
        if isinstance(v, list):
            result: List[MessageContent] = []
            for item in v:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(
                    item, (ImageContent, AudioContent, VideoContent, DocumentContent)
                ):
                    result.append(item)
                elif isinstance(item, dict):
                    # Reconstruct from serialized form
                    item_type = item.get("type", "")
                    if item_type in (
                        "text",
                        "input_text",
                        "output_text",
                        "summary_text",
                    ):
                        result.append(str(item.get("text", "")))
                    elif item_type in ("image", "input_image"):
                        result.append(ImageContent.model_validate(item))
                    elif item_type in ("audio", "input_audio"):
                        result.append(AudioContent.model_validate(item))
                    elif item_type in ("video", "input_video"):
                        result.append(VideoContent.model_validate(item))
                    elif item_type in ("document", "input_document", "input_file"):
                        result.append(DocumentContent.model_validate(item))
                    else:
                        result.append(str(item.get("text", str(item))))
                else:
                    result.append(str(item))
            return result
        raise ValueError("Content must be a list")


class ToolCallMessage(BaseClientMessage[Optional[str]]):
    """Represents a single tool call (MCP-compatible)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "tool_call"
    content: Optional[str] = None
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    type: Literal["ToolCallMessage"] = "ToolCallMessage"  # type: ignore[override]

    @property
    def tool_call_id(self) -> str:
        """Canonical tool-call correlation ID — same value as ``id``."""
        return self.id

    @field_validator("arguments", mode="before")
    @classmethod
    def _coerce_arguments(cls, v: object) -> JsonObject:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
            raise ValueError("arguments must be a dict or JSON string")
        if isinstance(v, dict):
            return v  # type: ignore[return-value]
        raise ValueError("arguments must be a dict")


class AssistantMessage(BaseClientMessage[Optional[List[MessageContent]]]):
    """Assistant message with optional tool calls.

    Provider-specific serialization is handled by the encoder modules
    in ``integrations/llm/encoders/``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: Literal["AssistantMessage"] = "AssistantMessage"  # type: ignore[override]
    role: CLIENT_ROLES = "assistant"
    name: Optional[str] = None
    reasoning: Optional[str] = None
    content: Optional[List[MessageContent]] = None
    tool_calls: Optional[List[ToolCallMessage]] = None
    finish_reason: str = "stop"
    usage: Optional[UsageStats] = None
    cached: bool = False
    parsed: Optional[PydanticBaseModel] = None

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, v: object) -> Optional[List[MessageContent]]:
        if v is None:
            return None
        if isinstance(v, list):
            result: List[MessageContent] = []
            for item in v:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(
                    item, (ImageContent, AudioContent, VideoContent, DocumentContent)
                ):
                    result.append(item)
                elif isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type in (
                        "text",
                        "input_text",
                        "output_text",
                        "summary_text",
                    ):
                        result.append(str(item.get("text", "")))
                    elif item_type in ("image", "input_image"):
                        result.append(ImageContent.model_validate(item))
                    elif item_type in ("audio", "input_audio"):
                        result.append(AudioContent.model_validate(item))
                    elif item_type in ("video", "input_video"):
                        result.append(VideoContent.model_validate(item))
                    elif item_type in ("document", "input_document", "input_file"):
                        result.append(DocumentContent.model_validate(item))
                    else:
                        result.append(str(item.get("text", str(item))))
                else:
                    result.append(str(item))
            return result
        raise ValueError("Content must be a list or None")


class ToolExecutionResultMessage(BaseClientMessage[List[ContentBlock]]):
    """Tool execution result message (MCP-compatible).

    Content is a list of typed ``ContentBlock`` objects instead of raw
    dicts — fully typed and serializable.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: CLIENT_ROLES = "tool_response"
    tool_call_id: str
    name: Optional[str] = None
    content: List[ContentBlock]
    is_error: bool = False
    app_data: Optional[JsonObject] = None
    media: Optional[List[MediaContent]] = None
    type: Literal["ToolExecutionResultMessage"] = "ToolExecutionResultMessage"  # type: ignore[override]

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, v: object) -> List[ContentBlock]:
        """Coerce various input formats to typed ContentBlock list."""
        if isinstance(v, str):
            return [TextBlock(text=v)]
        if isinstance(v, list):
            result: list[ContentBlock] = []
            for item in v:
                if isinstance(item, CONTENT_BLOCK_TYPES):
                    result.append(item)
                elif isinstance(item, dict):
                    result.append(content_block_from_dict(item))
                elif isinstance(item, str):
                    result.append(TextBlock(text=item))
                else:
                    result.append(TextBlock(text=str(item)))
            return result
        if isinstance(v, dict):
            return [content_block_from_dict(v)]
        return [TextBlock(text=str(v))]

    @field_validator("media", mode="before")
    @classmethod
    def _coerce_media(cls, v: object) -> Optional[List[MediaContent]]:
        if v is None:
            return None
        if isinstance(v, list):
            result: List[MediaContent] = []
            for item in v:
                if isinstance(
                    item, (ImageContent, AudioContent, VideoContent, DocumentContent)
                ):
                    result.append(item)
                elif isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "image":
                        result.append(ImageContent.model_validate(item))
                    elif item_type == "audio":
                        result.append(AudioContent.model_validate(item))
                    elif item_type == "video":
                        result.append(VideoContent.model_validate(item))
                    elif item_type == "document":
                        result.append(DocumentContent.model_validate(item))
                # Skip unrecognized items
            return result
        raise ValueError("media must be a list or None")

