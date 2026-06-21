"""Content blocks — the universal multimodal payload primitive.

Every agent message, tool result, and pub/sub event carries a
``list[ContentBlock]``.  Blocks are:

- Fully serializable: ``block.model_dump(mode="json")`` ↔ ``content_block_from_dict()``
- Self-describing: discriminated on the ``type`` literal field
- Self-rendering: every block has ``to_text_repr() -> str``

Binary media (images, audio, video, documents) is encoded as **base64** when
serialized to JSON.  ``ser_json_bytes="base64"`` / ``val_json_bytes="base64"``
on each model config handles the round-trip automatically.

Adding a new block type:
  1. Define the class (frozen pydantic model, Literal ``type`` field).
  2. Call ``register_block_type(YourBlock)`` — or add to the curated union
     below if it is a first-class protocol primitive.
  3. Add to ``__all__``.

Provider adapters in ``integrations/`` handle the final wire-format conversion
for each LLM API.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Role enum — standard conversation roles
# ---------------------------------------------------------------------------


class Role(StrEnum):
    """Standard conversation turn roles.

    Using the enum ensures consistent role strings across the codebase.
    Because ``Role`` is a ``StrEnum``, ``role == "user"`` comparisons work
    and string literals are accepted wherever ``Role`` is expected.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


# ---------------------------------------------------------------------------
# JSON primitive types
# ---------------------------------------------------------------------------

JsonObject = dict[str, Any]
"""A JSON-serializable string-keyed mapping."""


# ---------------------------------------------------------------------------
# Validation error
# ---------------------------------------------------------------------------


class BlockValidationError(ValueError):
    """Raised when a known block type fails schema validation.

    Distinct from ``UnknownBlock`` (which handles completely unknown types).
    Callers can catch this to handle corrupt or version-mismatched blocks.
    """


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
    schema_id: str | None = None

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        import json as _json

        return _json.dumps(self.data)


class ErrorBlock(BaseModel):
    """Typed error — use instead of TextBlock when a tool fails.

    Lets consumers detect and route errors programmatically without
    string-matching on message text.
    """

    type: Literal["error"] = "error"
    error_type: str
    message: str
    details: JsonObject | None = None
    recoverable: bool = True

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        return f"[{self.error_type}]: {self.message}"


# ---------------------------------------------------------------------------
# Media blocks — unified (URL | bytes | file_id)
# ---------------------------------------------------------------------------

_MEDIA_CONFIG = {
    "frozen": True,
    "ser_json_bytes": "base64",
    "val_json_bytes": "base64",
}


class ImageBlock(BaseModel):
    """Image — URL reference, raw bytes, or provider file-ID.

    Exactly one of ``url``, ``data``, or ``file_id`` must be set.
    ``data`` is raw bytes; serializes as base64 in JSON.
    """

    type: Literal["image"] = "image"
    url: str | None = None
    data: bytes | None = None
    file_id: str | None = None
    media_type: str = "image/jpeg"

    model_config = _MEDIA_CONFIG  # type: ignore[assignment]

    @model_validator(mode="after")
    def _one_source(self) -> "ImageBlock":
        if sum(x is not None for x in (self.url, self.data, self.file_id)) != 1:
            raise ValueError("Exactly one of url, data, or file_id must be provided")
        return self

    def to_text_repr(self) -> str:
        ref = self.url or (self.file_id and f"file:{self.file_id}") or self.media_type
        return f"[Image: {ref}]"


class AudioBlock(BaseModel):
    """Audio — URL reference or raw bytes.

    At least one of ``url`` or ``data`` must be provided.
    """

    type: Literal["audio"] = "audio"
    url: str | None = None
    data: bytes | None = None
    media_type: str = "audio/wav"
    transcript: str | None = None

    model_config = _MEDIA_CONFIG  # type: ignore[assignment]

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
    """Video — URL reference or raw bytes.

    At least one of ``url`` or ``data`` must be provided.
    """

    type: Literal["video"] = "video"
    url: str | None = None
    data: bytes | None = None
    media_type: str = "video/mp4"

    model_config = _MEDIA_CONFIG  # type: ignore[assignment]

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
    url: str | None = None
    data: bytes | None = None
    file_id: str | None = None
    media_type: str = "application/pdf"
    filename: str | None = None

    model_config = _MEDIA_CONFIG  # type: ignore[assignment]

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

    ``name`` identifies which tool produced the result, enabling
    attribution and routing without inspecting ``call_id`` alone.
    """

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str = ""
    content: list["ContentBlock"] = Field(default_factory=list)
    is_error: bool = False

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        inner = content_blocks_to_str(self.content) if self.content else "(empty)"
        prefix = "[ToolError" if self.is_error else "[ToolResult"
        return f"{prefix}: {self.call_id}] {inner}"


class ThinkingBlock(BaseModel):
    """Agent reasoning / chain-of-thought trace.

    Extended-thinking models expose thinking tokens.
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


class UIResourceBlock(BaseModel):
    """An interactive UI rendered in a sandboxed iframe.

    The narrow waist for any rich tool UI. A tool emits a single
    self-describing reference: a ``ui://`` resource URI plus opaque
    structured data. The host renders the resource in a sandboxed
    iframe; ``structured_content`` is UI-facing and model-invisible.

    - ``uri``                 the ``ui://name`` resource to render
    - ``structured_content``  opaque data passed to the iframe
    - ``text``                model-facing fallback (LLM cannot see pixels)
    - ``render``              host placement hint
    """

    type: Literal["ui_resource"] = "ui_resource"
    uri: str
    mime_type: str = "text/html;profile=mcp-app"
    structured_content: JsonObject = Field(default_factory=dict)
    text: str = ""
    render: Literal["inline", "panel", "fullscreen"] = "inline"

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        return self.text or f"[interactive UI: {self.uri}]"


class UnknownBlock(BaseModel):
    """Lossless carrier for block types not recognized by this version.

    Returned by ``content_block_from_dict`` when the ``type`` field
    does not match any registered block.  Preserves the raw payload
    so mixed-version distributed deployments do not silently corrupt data.
    """

    type: Literal["unknown"] = "unknown"
    raw: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        original_type = self.raw.get("type", "?")
        return f"[UnknownBlock: {original_type}]"


# ---------------------------------------------------------------------------
# ChatMessage — the role-tagged conversation turn
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A role-tagged conversation turn containing multimodal blocks.

    ``role`` follows the ``Role`` enum convention but accepts any string
    for forward-compatibility with providers that define custom roles.

    ``name`` identifies the participant within a multi-agent conversation
    (e.g. agent name, user handle) for attribution and routing.
    """

    role: str
    content: list["ContentBlock"] = Field(default_factory=list)
    name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ContentBlock — the discriminated union
# ---------------------------------------------------------------------------

ContentBlock = Annotated[
    TextBlock
    | ImageBlock
    | AudioBlock
    | VideoBlock
    | DocumentBlock
    | DataBlock
    | CodeBlock
    | ErrorBlock
    | ToolUseBlock
    | ToolResultBlock
    | ThinkingBlock
    | UIResourceBlock,
    Field(discriminator="type"),
]
"""Universal multimodal payload primitive.

Every agent message and tool result is a ``list[ContentBlock]``.
"""

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
    UIResourceBlock,
)

# ---------------------------------------------------------------------------
# Block registry — extensible, public
# ---------------------------------------------------------------------------

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
    "ui_resource": UIResourceBlock,
}


def register_block_type(cls: type[BaseModel]) -> None:
    """Register a custom block type for use in ``content_block_from_dict``.

    ``cls`` must have a ``type`` class attribute (the string discriminator).
    Call this once at module load time, before any deserialization happens.

    Example::

        class ChartBlock(BaseModel):
            type: Literal["chart"] = "chart"
            data: dict

        register_block_type(ChartBlock)
    """
    type_name = getattr(cls, "type", None)
    if type_name is None:
        type_name = getattr(cls.model_fields.get("type"), "default", None)
    if not isinstance(type_name, str):
        raise TypeError(f"{cls.__name__} must have a string 'type' class attribute")
    _BLOCK_REGISTRY[type_name] = cls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def content_block_from_dict(data: dict[str, object]) -> ContentBlock | UnknownBlock:
    """Deserialize a raw dict to the correct ContentBlock variant.

    - Known type, valid data → the typed block.
    - Known type, invalid data → raises ``BlockValidationError``.
    - Unknown type → ``UnknownBlock`` preserving the raw payload (no data loss).
    """
    block_type = str(data.get("type", ""))
    cls = _BLOCK_REGISTRY.get(block_type)
    if cls is None:
        return UnknownBlock(raw=dict(data))  # type: ignore[arg-type]
    try:
        return cls.model_validate(data)  # type: ignore[return-value]
    except Exception as exc:
        raise BlockValidationError(
            f"Failed to validate block of type {block_type!r}: {exc}"
        ) from exc


def content_blocks_to_str(blocks: list[ContentBlock | UnknownBlock]) -> str:
    """Human-readable string from a list of content blocks."""
    return "\n".join(
        block.to_text_repr() if hasattr(block, "to_text_repr") else str(block)
        for block in blocks
    )


ToolResultBlock.model_rebuild()
ChatMessage.model_rebuild()

__all__ = [
    "Role",
    "JsonObject",
    "BlockValidationError",
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
    "UIResourceBlock",
    "UnknownBlock",
    "ContentBlock",
    "CONTENT_BLOCK_TYPES",
    "register_block_type",
    "content_block_from_dict",
    "content_blocks_to_str",
    "ChatMessage",
]
