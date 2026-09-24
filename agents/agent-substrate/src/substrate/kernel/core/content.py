"""Universal multimodal content blocks and conversation messages.

Zero-boilerplate, immutable data primitives powered natively by Pydantic v2.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    TypeAdapter,
    model_validator,
)
from substrate.kernel.exceptions import BlockValidationError

JsonObject = dict[str, Any]


class Role(StrEnum):
    """Standard conversation turn roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class KernelModel(BaseModel):
    """Base model for all kernel content structures — immutable with standard string representation."""

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Text, Data & Error Blocks
# ---------------------------------------------------------------------------


class TextBlock(KernelModel):
    """Plain text or markdown content."""

    type: Literal["text"] = "text"
    text: str

    def __str__(self) -> str:
        return self.text


class DataBlock(KernelModel):
    """Structured JSON payload for rich tool results or artifacts."""

    type: Literal["data"] = "data"
    data: JsonObject
    schema_id: str | None = None

    def __str__(self) -> str:
        return json.dumps(self.data)


class ErrorBlock(KernelModel):
    """Typed error block for structured failure reporting."""

    type: Literal["error"] = "error"
    error_type: str
    message: str
    details: JsonObject | None = None
    recoverable: bool = True

    def __str__(self) -> str:
        return f"[{self.error_type}]: {self.message}"


class ReasoningBlock(KernelModel):
    """Model thinking trace and cryptographic verification signature."""

    type: Literal["reasoning"] = "reasoning"
    text: str
    signature: str | None = None
    redacted: bool = False

    def __str__(self) -> str:
        return "[Reasoning: redacted]" if self.redacted else f"[Reasoning] {self.text}"


# ---------------------------------------------------------------------------
# Unified Media Primitive (Sole Binary Carrier)
# ---------------------------------------------------------------------------


class MediaBlock(KernelModel):
    """Unified binary media primitive for images, audio, video, and documents."""

    type: Literal["image", "audio", "video", "document"]
    media_type: str = "application/octet-stream"
    url: str | None = None
    data: bytes | None = None  # Encoded as base64 in JSON mode
    file_id: str | None = None
    detail: Literal["low", "high", "auto"] = "auto"
    filename: str | None = None
    transcript: str | None = None
    storage_key: str | None = None

    model_config = {
        "frozen": True,
        "ser_json_bytes": "base64",
        "val_json_bytes": "base64",
    }

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        if sum(x is not None for x in (self.url, self.data, self.file_id)) != 1:
            raise ValueError("Exactly one of url, data, or file_id must be provided")
        return self

    @classmethod
    def image(
        cls,
        *,
        url: str | None = None,
        data: bytes | None = None,
        file_id: str | None = None,
        media_type: str = "image/jpeg",
        **kwargs: Any,
    ) -> MediaBlock:
        return cls(type="image", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs)

    @classmethod
    def audio(
        cls,
        *,
        url: str | None = None,
        data: bytes | None = None,
        file_id: str | None = None,
        media_type: str = "audio/wav",
        **kwargs: Any,
    ) -> MediaBlock:
        return cls(type="audio", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs)

    @classmethod
    def video(
        cls,
        *,
        url: str | None = None,
        data: bytes | None = None,
        file_id: str | None = None,
        media_type: str = "video/mp4",
        **kwargs: Any,
    ) -> MediaBlock:
        return cls(type="video", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs)

    @classmethod
    def document(
        cls,
        *,
        url: str | None = None,
        data: bytes | None = None,
        file_id: str | None = None,
        media_type: str = "application/pdf",
        **kwargs: Any,
    ) -> MediaBlock:
        return cls(type="document", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs)

    @property
    def is_image(self) -> bool:
        return self.type == "image"

    @property
    def is_audio(self) -> bool:
        return self.type == "audio"

    @property
    def is_video(self) -> bool:
        return self.type == "video"

    @property
    def is_document(self) -> bool:
        return self.type == "document"

    def __str__(self) -> str:
        if self.transcript:
            return f"[Audio transcript: {self.transcript}]"
        ref = (
            self.filename
            or self.url
            or (f"file:{self.file_id}" if self.file_id else self.media_type)
        )
        return f"[{self.type.capitalize()}: {ref}]"


# ---------------------------------------------------------------------------
# Tool Invocations, Results & Forward Compatibility
# ---------------------------------------------------------------------------


class ToolUseBlock(KernelModel):
    """Tool invocation request."""

    type: Literal["tool_use"] = "tool_use"
    call_id: str
    tool_name: str
    arguments: JsonObject = Field(default_factory=dict)
    # Set by an LLM client when the model's arguments weren't valid JSON —
    # the harness reports it back to the model instead of running the tool.
    arguments_error: str | None = None

    def __str__(self) -> str:
        return f"[ToolCall: {self.tool_name}({self.call_id})]"


class UnknownBlock(KernelModel):
    """Lossless carrier for forward-compatibility with future provider types."""

    type: Literal["unknown"] = "unknown"
    raw: JsonObject = Field(default_factory=dict)

    def __str__(self) -> str:
        return f"[UnknownBlock: {self.raw.get('type', '?')}]"


def _coerce_blocks(v: Any) -> list[Any]:
    if isinstance(v, str):
        return [TextBlock(text=v)]
    if isinstance(v, (BaseModel, dict)):
        v = [v]
    if isinstance(v, list):
        return [parse_content_block(item) if isinstance(item, dict) else item for item in v]
    return v


BlockList = Annotated[list["ContentBlock"], BeforeValidator(_coerce_blocks)]


class ToolResultBlock(KernelModel):
    """Multimodal tool execution outcome."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str = ""
    content: BlockList = Field(default_factory=list)
    is_error: bool = False

    @property
    def text(self) -> str:
        """Concatenated plain text of all TextBlock items."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def __str__(self) -> str:
        inner = "\n".join(str(b) for b in self.content)
        prefix = "[ToolError" if self.is_error else "[ToolResult"
        return f"{prefix}: {self.call_id}] {inner}" if inner else f"{prefix}: {self.call_id}] (empty)"


# ---------------------------------------------------------------------------
# Discriminated Union & Single Rust-Powered Parser
# ---------------------------------------------------------------------------

_KNOWN_BLOCK_TAGS = {
    "text",
    "image",
    "audio",
    "video",
    "document",
    "data",
    "error",
    "reasoning",
    "tool_use",
    "tool_result",
    "unknown",
}

ContentBlock = Annotated[
    Union[
        TextBlock,
        MediaBlock,
        DataBlock,
        ErrorBlock,
        ReasoningBlock,
        ToolUseBlock,
        ToolResultBlock,
        UnknownBlock,
    ],
    Field(discriminator="type"),
]

ContentBlockAdapter: TypeAdapter[ContentBlock] = TypeAdapter(ContentBlock)
"""The single, canonical, Rust-powered TypeAdapter for ContentBlock."""


def parse_content_block(data: Any) -> ContentBlock:
    """The single efficient way to parse any dict or object into a ContentBlock."""
    if isinstance(data, dict) and str(data.get("type")) not in _KNOWN_BLOCK_TAGS:
        return UnknownBlock(raw=dict(data))
    try:
        return ContentBlockAdapter.validate_python(data)
    except Exception as exc:
        raise BlockValidationError(f"Failed to validate content block: {exc}") from exc


def content_blocks_to_str(blocks: Sequence[ContentBlock]) -> str:
    """Human-readable string representation of content blocks."""
    return "\n".join(str(b) for b in blocks)



# ---------------------------------------------------------------------------
# ChatMessage (Role-tagged conversation turn)
# ---------------------------------------------------------------------------


class ChatMessage(KernelModel):
    """A role-tagged conversation turn containing multimodal blocks."""

    role: Role
    content: BlockList = Field(default_factory=list)
    name: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    @property
    def text(self) -> str:
        """Concatenated text of all TextBlock items in content."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_calls(self) -> list[ToolUseBlock]:
        """All tool use requests in this message."""
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def reasoning(self) -> str:
        """Concatenated model thinking traces."""
        return "\n".join(b.text for b in self.content if isinstance(b, ReasoningBlock))

    def __str__(self) -> str:
        return f"{self.role}: {self.text}"


ToolResultBlock.model_rebuild()
ChatMessage.model_rebuild()

__all__ = [
    "Role",
    "JsonObject",
    "KernelModel",
    "TextBlock",
    "MediaBlock",
    "DataBlock",
    "ErrorBlock",
    "ReasoningBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "UnknownBlock",
    "ContentBlock",
    "ContentBlockAdapter",
    "parse_content_block",
    "content_blocks_to_str",
    "ChatMessage",
]
