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
  2. Add it to the ``ContentBlock`` discriminated union and ``_BLOCK_REGISTRY``
     below.
  3. Add to ``__all__``.

Provider adapters in ``integrations/`` handle the final wire-format conversion
for each LLM API.
"""

from __future__ import annotations

from enum import StrEnum
from collections.abc import Sequence
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

    ``detail`` is OpenAI's vision resolution hint (``"low"``/``"high"``/
    ``"auto"``); providers that don't support it simply ignore it.

    ``storage_key`` is provenance, not a fourth content source: where these
    bytes came from in the file store, when they came from one. It exists so a
    consumer that only needs to *reference* the image can emit a durable link
    instead of copying the bytes — chiefly the wire-event log, which otherwise
    inlines a base64 copy of every image on every tool call. It is deliberately
    separate from ``url``: provider encoders prefer ``url`` and would send it
    upstream, and a storage key is not something a model provider can fetch.
    """

    type: Literal["image"] = "image"
    url: str | None = None
    data: bytes | None = None
    file_id: str | None = None
    storage_key: str | None = None
    media_type: str = "image/jpeg"
    detail: Literal["low", "high", "auto"] = "auto"

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


class ReasoningBlock(BaseModel):
    """Model reasoning / extended-thinking trace, persisted in message content.

    Emitted live as a transient ``ReasoningDelta`` during streaming; this is
    the durable form that lands in ``ChatMessage.content`` so reasoning
    survives history replay and — critically — can be sent back to the
    provider on continuation.

    ``signature`` carries the provider's opaque verification token (Anthropic
    extended thinking returns one per thinking block; it MUST be replayed
    verbatim in the assistant turn when continuing a tool-use loop with
    thinking enabled, or the API rejects the request). ``None`` for providers
    that don't sign reasoning (e.g. OpenAI reasoning summaries).

    ``redacted`` marks a block the provider returned in encrypted/redacted
    form (Anthropic ``redacted_thinking``): the ``text`` is not human-readable
    but must still be round-tripped intact for continuation.
    """

    type: Literal["reasoning"] = "reasoning"
    text: str
    signature: str | None = None
    redacted: bool = False

    model_config = {"frozen": True}

    def to_text_repr(self) -> str:
        if self.redacted:
            return "[Reasoning: redacted]"
        return f"[Reasoning] {self.text}"


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
    | ReasoningBlock
    | ToolUseBlock
    | ToolResultBlock,
    Field(discriminator="type"),
]
"""Universal multimodal payload primitive.

Every agent message and tool result is a ``list[ContentBlock]``.
"""

# ---------------------------------------------------------------------------
# Block registry
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
    "reasoning": ReasoningBlock,
    "tool_use": ToolUseBlock,
    "tool_result": ToolResultBlock,
}


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


def content_blocks_to_str(blocks: Sequence[ContentBlock | UnknownBlock]) -> str:
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
    "ReasoningBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "UnknownBlock",
    "ContentBlock",
    "content_block_from_dict",
    "content_blocks_to_str",
    "ChatMessage",
]
