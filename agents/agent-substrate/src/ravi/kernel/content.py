"""Content blocks — the universal multimodal payload primitive.

Every agent message, tool result, and pub/sub event carries a
``list[ContentBlock]``.  Blocks are:

- Fully serializable: ``block.model_dump(mode="json")`` ↔ ``content_block_from_dict()``
- Self-describing: discriminated on the ``type`` literal field
- Self-rendering: every block has ``to_text_repr() -> str``

Media blocks (image, audio, video, document) are unified — a single block type
carries URL references, raw bytes, or provider file-IDs.  Provider encoders in
``fabric/`` handle the final wire-format conversion.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON primitive types
# ---------------------------------------------------------------------------

JsonObject = dict[str, Any]
"""A JSON-serializable string-keyed mapping.  Use instead of ``Dict[str, Any]``
to signal intent: "this holds structured data", not "literally anything"."""


# ---------------------------------------------------------------------------
# Text / Code / Data / Error blocks
# ---------------------------------------------------------------------------


class TextBlock(BaseModel):
    """Plain text."""

    type: Literal["text"] = "text"
    text: str

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        return self.text


class CodeBlock(BaseModel):
    """Language-tagged source code."""

    type: Literal["code"] = "code"
    code: str
    language: str = "python"

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        return f"```{self.language}\n{self.code}\n```"


class DataBlock(BaseModel):
    """Structured JSON payload — for rich tool outputs."""

    type: Literal["data"] = "data"
    data: JsonObject
    schema_id: Optional[str] = None  # optional JSON Schema reference

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        import json as _json
        return _json.dumps(self.data)


class ErrorBlock(BaseModel):
    """Typed error — use instead of TextBlock when a tool fails.

    Lets consumers detect and route errors programmatically without
    string-matching.
    """

    type: Literal["error"] = "error"
    error_type: str       # e.g. "ValueError", "TimeoutError"
    message: str
    details: Optional[JsonObject] = None
    recoverable: bool = True

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        return f"[{self.error_type}]: {self.message}"


# ---------------------------------------------------------------------------
# Media blocks — unified (URL | bytes | file_id)
# ---------------------------------------------------------------------------


class ImageBlock(BaseModel):
    """Image — URL reference, raw bytes, or provider file-ID.

    Exactly one of ``url``, ``data``, or ``file_id`` must be set.

    ``detail`` is an optional rendering hint (e.g. OpenAI vision quality).
    Provider encoders decide what to do with it.
    """

    type: Literal["image"] = "image"
    url: Optional[str] = None
    data: Optional[bytes] = None    # raw bytes — Pydantic auto-serializes as base64
    file_id: Optional[str] = None  # provider-uploaded file reference
    media_type: str = "image/jpeg"
    detail: Literal["low", "high", "auto"] = "auto"

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _one_source(self) -> "ImageBlock":
        if sum(x is not None for x in (self.url, self.data, self.file_id)) != 1:
            raise ValueError("Exactly one of url, data, or file_id must be provided")
        return self

    def to_text_repr(self) -> str:
        ref = self.url or (self.file_id and f"file:{self.file_id}") or self.media_type
        return f"[Image: {ref}]"


class AudioBlock(BaseModel):
    """Audio — URL reference or raw bytes."""

    type: Literal["audio"] = "audio"
    url: Optional[str] = None
    data: Optional[bytes] = None
    media_type: str = "audio/wav"
    transcript: Optional[str] = None  # optional pre-transcribed text

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _one_source(self) -> "AudioBlock":
        if self.url is None and self.data is None:
            raise ValueError("At least one of url or data must be provided")
        return self

    def to_text_repr(self) -> str:
        if self.transcript:
            return f"[Audio transcript: {self.transcript}]"
        return f"[Audio: {self.url or self.media_type}]"


class VideoBlock(BaseModel):
    """Video — URL reference or raw bytes."""

    type: Literal["video"] = "video"
    url: Optional[str] = None
    data: Optional[bytes] = None
    media_type: str = "video/mp4"

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _one_source(self) -> "VideoBlock":
        if self.url is None and self.data is None:
            raise ValueError("At least one of url or data must be provided")
        return self

    def to_text_repr(self) -> str:
        return f"[Video: {self.url or self.media_type}]"


class DocumentBlock(BaseModel):
    """Document — URL reference, raw bytes, or provider file-ID.

    Exactly one of ``url``, ``data``, or ``file_id`` must be set.
    """

    type: Literal["document"] = "document"
    url: Optional[str] = None
    data: Optional[bytes] = None
    file_id: Optional[str] = None
    media_type: str = "application/pdf"
    filename: Optional[str] = None

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _one_source(self) -> "DocumentBlock":
        if sum(x is not None for x in (self.url, self.data, self.file_id)) != 1:
            raise ValueError("Exactly one of url, data, or file_id must be provided")
        return self

    def to_text_repr(self) -> str:
        return f"[Document: {self.filename or self.url or self.media_type}]"


# ---------------------------------------------------------------------------
# Agentic blocks — tool calls, results, reasoning
# ---------------------------------------------------------------------------


class ToolUseBlock(BaseModel):
    """Tool invocation request — inline in a content stream."""

    type: Literal["tool_use"] = "tool_use"
    call_id: str
    tool_name: str
    arguments: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        return f"[ToolCall: {self.tool_name}({self.call_id})]"


class ToolResultBlock(BaseModel):
    """Result of a tool invocation — itself multimodal.

    ``content`` is a list of ContentBlock so a tool can return
    text, images, code, data, errors, or any combination.
    """

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    content: list["ContentBlock"] = Field(default_factory=list)
    is_error: bool = False

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        inner = content_blocks_to_str(self.content) if self.content else "(empty)"
        prefix = "[ToolError" if self.is_error else "[ToolResult"
        return f"{prefix}: {self.call_id}] {inner}"


class ThinkingBlock(BaseModel):
    """Agent reasoning / chain-of-thought trace.

    Extended-thinking models (e.g. Claude) expose thinking tokens.
    Storing them typed lets consumers render, redact, or skip cleanly.
    """

    type: Literal["thinking"] = "thinking"
    text: str
    redacted: bool = False

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        if self.redacted:
            return "[Thinking: redacted]"
        return f"[Thinking] {self.text}"


# ---------------------------------------------------------------------------
# ContentBlock — the discriminated union
# ---------------------------------------------------------------------------

ContentBlock = Annotated[
    Union[
        TextBlock,
        ImageBlock,
        AudioBlock,
        VideoBlock,
        DocumentBlock,
        DataBlock,
        CodeBlock,
        ErrorBlock,
        ToolUseBlock,
        ToolResultBlock,
        ThinkingBlock,
    ],
    Field(discriminator="type"),
]
"""Universal multimodal payload primitive.

Every agent message and tool result is a ``list[ContentBlock]``.
"""

# Tuple of all concrete block classes — for isinstance checks at
# deserialization boundaries (cheaper and safer than duck-typing on .type).
CONTENT_BLOCK_TYPES: tuple[type, ...] = (
    TextBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    DocumentBlock,
    DataBlock,
    CodeBlock,
    ErrorBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Registry: type literal → model class.  Adding a new block requires only:
# (1) define the class, (2) add to ContentBlock union,
# (3) add to CONTENT_BLOCK_TYPES, (4) add one line here.
_BLOCK_REGISTRY: dict[str, type[BaseModel]] = {
    "text": TextBlock,
    "image": ImageBlock,
    "audio": AudioBlock,
    "video": VideoBlock,
    "document": DocumentBlock,
    "data": DataBlock,
    "code": CodeBlock,
    "error": ErrorBlock,
    "tool_use": ToolUseBlock,
    "tool_result": ToolResultBlock,
    "thinking": ThinkingBlock,
}


def content_block_from_dict(data: dict[str, object]) -> ContentBlock:
    """Deserialize a raw dict to the correct ContentBlock variant.

    Dispatches on the ``type`` field.  Unknown types fall back to TextBlock.
    """
    block_type = str(data.get("type", "text"))
    cls = _BLOCK_REGISTRY.get(block_type, TextBlock)
    try:
        return cls.model_validate(data)  # type: ignore[return-value]
    except Exception:
        logger.warning(
            "Failed to validate %r as %s; falling back to TextBlock.",
            block_type,
            cls.__name__,
        )
        return TextBlock(text=str(data))


def content_blocks_to_str(blocks: list[ContentBlock]) -> str:
    """Human-readable string from a list of content blocks.

    Each block provides its own representation via ``to_text_repr()``.
    """
    return "\n".join(
        block.to_text_repr() if hasattr(block, "to_text_repr") else str(block)
        for block in blocks
    )


# Resolve the forward reference in ToolResultBlock.
ToolResultBlock.model_rebuild()

__all__ = [
    "JsonObject",
    "TextBlock",
    "ImageBlock",
    "AudioBlock",
    "VideoBlock",
    "DocumentBlock",
    "DataBlock",
    "CodeBlock",
    "ErrorBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ThinkingBlock",
    "ContentBlock",
    "CONTENT_BLOCK_TYPES",
    "content_block_from_dict",
    "content_blocks_to_str",
]
