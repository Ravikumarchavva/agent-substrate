"""Typed content blocks and media content models.

Single source of truth for all content representations in the framework.
All types are Pydantic models — fully serializable, validatable, and
round-trippable via ``model_dump(mode="json")`` / ``model_validate()``.

Design decisions:
- **No PIL.Image.Image** — use ``ImageContent(data=..., media_type=...)``
  for raw bytes.  Provider encoders handle conversion to API formats.
- **Discriminated unions** via ``type`` field — enables Pydantic to
  auto-route deserialization without manual ``isinstance`` branching.
- **ContentBlock** = what tool results carry (MCP-compatible).
- **MediaContent** = typed media attachments (images, audio, video).
- **MessageContent** = Union[str, MediaContent] — what messages carry.
- **JsonValue / JsonObject** = replaces ``Dict[str, Any]`` for truly
  dynamic data (tool args, metadata).
"""

from __future__ import annotations

import base64
import logging
from typing import Annotated, Literal, Optional, Union, Any

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)



# ── JSON value types (replaces Dict[str, Any] everywhere) ────────────────
# NOTE: Pydantic < 2.10 on Python 3.11 cannot handle truly recursive type
# aliases. We use ``Any`` as the value type but constrain the key type to
# ``str`` for all JSON object fields. This is still much better than bare
# ``Dict[str, Any]`` because it communicates intent: "this field holds
# JSON-serializable data" vs "this field can hold literally anything".

JsonPrimitive = Union[str, int, float, bool, None]
JsonValue = Any  # Conceptually: Union[JsonPrimitive, list[JsonValue], dict[str, JsonValue]]
JsonObject = dict[str, Any]  # Conceptually: dict[str, JsonValue]


# ── Content Blocks (tool results, MCP-compatible) ────────────────────────


class TextBlock(BaseModel):
    """Plain text content block."""

    type: Literal["text"] = "text"
    text: str

    model_config = {"frozen": True}


class ImageBlock(BaseModel):
    """Base64-encoded image content block."""

    type: Literal["image"] = "image"
    data: str  # base64-encoded
    media_type: str = "image/png"

    model_config = {"frozen": True}


# ── MCP resource block (not part of ContentBlock — archived MCP compat) ──────
class ResourceBlock(BaseModel):
    """URI-referenced resource content block (MCP resources)."""

    type: Literal["resource"] = "resource"
    uri: str
    mime_type: Optional[str] = None
    text: Optional[str] = None

    model_config = {"frozen": True}


class AudioBlock(BaseModel):
    """Base64-encoded audio content block for tool outputs."""

    type: Literal["audio"] = "audio"
    data: str  # base64-encoded bytes
    media_type: str = "audio/wav"  # audio/wav, audio/mp3, audio/ogg, audio/flac
    transcript: Optional[str] = None  # optional pre-transcribed text

    model_config = {"frozen": True}


class VideoBlock(BaseModel):
    """Video content block for tool outputs — URL-referenced or base64-encoded."""

    type: Literal["video"] = "video"
    url: Optional[str] = None  # URL reference
    data: Optional[str] = None  # base64-encoded bytes
    media_type: str = "video/mp4"

    model_config = {"frozen": True}


class DocumentBlock(BaseModel):
    """Base64-encoded document content block (PDF, HTML, DOCX, etc.)."""

    type: Literal["document"] = "document"
    data: str  # base64-encoded bytes
    media_type: str  # "application/pdf", "text/html", "text/plain", etc.
    filename: Optional[str] = None

    model_config = {"frozen": True}


class DataBlock(BaseModel):
    """Structured JSON payload content block for tool outputs."""

    type: Literal["data"] = "data"
    data: JsonObject  # JSON-serializable dict
    schema_id: Optional[str] = None  # optional reference to a JSON Schema identifier

    model_config = {"frozen": True}


class CodeBlock(BaseModel):
    """Code content block — language-tagged code snippet for tool outputs."""

    type: Literal["code"] = "code"
    code: str
    language: str = "python"

    model_config = {"frozen": True}


class ErrorBlock(BaseModel):
    """Structured error content block for tool outputs.

    Use instead of TextBlock when the tool result represents a failure so that
    consumers can programmatically detect and route errors without string parsing.
    """

    type: Literal["error"] = "error"
    error_type: str  # e.g. "ValueError", "TimeoutError", "PermissionError"
    message: str
    details: Optional[JsonObject] = None
    recoverable: bool = True

    model_config = {"frozen": True}


# ── Agentic content blocks (tool calls, results, reasoning) ─────────────


class ToolUseBlock(BaseModel):
    """Represents a tool invocation request within a content stream.

    Carries the full tool call inline so that the message history is
    self-describing — no side-channel lookup needed.
    """

    type: Literal["tool_use"] = "tool_use"
    tool_call_id: str
    tool_name: str
    arguments: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}


class ToolResultBlock(BaseModel):
    """Result of a tool invocation — itself multimodal.

    ``content`` is a recursive list of ContentBlock, so a tool can return
    images, code, data, errors, or any combination.
    """

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    # NOTE: Forward-referenced as string to avoid circular definition.
    # At runtime Pydantic resolves it via model_rebuild().
    content: list["ContentBlock"] = Field(default_factory=list)
    is_error: bool = False

    model_config = {"frozen": True}


class ThinkingBlock(BaseModel):
    """Agent's reasoning / chain-of-thought trace.

    Models like Claude expose thinking tokens.  Storing them as a typed
    block lets consumers render, redact, or skip them cleanly.
    """

    type: Literal["thinking"] = "thinking"
    text: str
    redacted: bool = False

    model_config = {"frozen": True}


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
"""Discriminated union of all content block types.

This is the universal multimodal primitive — every message, tool result,
and agent-to-agent communication is a ``list[ContentBlock]``.
"""

# Concrete tuple of every ContentBlock variant — use for ``isinstance`` checks
# at runtime boundaries (e.g. validating user-supplied payloads). Cheaper and
# safer than duck-typing on ``.type`` / ``.model_dump``: a stale dict, a stray
# Pydantic model from another schema, or an arbitrary class with a ``.type``
# attribute all fail this check.
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


def blocks_to_text(blocks: list, *, separator: str = " ") -> str:
    """Concatenate the ``text`` field of every text-bearing block in *blocks*.

    Non-text blocks (images, audio, video, documents, errors, …) are
    represented by a short ``[Type]`` placeholder so the result is always
    a meaningful string — never a Python ``repr``.

    This is the canonical way to lower a multimodal envelope to a string
    when a caller (e.g. a single-turn ``BaseAgent.run`` adapter) requires
    a flat text input. Always prefer the original ``list[ContentBlock]``
    when the API can accept it.
    """
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, str):
            parts.append(block)
        elif hasattr(block, "type"):
            # Pydantic discriminated block — preserve its type as a placeholder
            parts.append(f"[{block.type}]")
        else:
            parts.append(str(block))
    return separator.join(parts)


# ── Media Content (message attachments) ──────────────────────────────────


class ImageContent(BaseModel):
    """Image input — URL, file-ID, or raw bytes.

    Exactly one of ``url``, ``file_id``, or ``data`` must be provided.

    Examples::

        ImageContent(url="https://example.com/photo.jpg", detail="high")
        ImageContent(file_id="file-abc123")
        ImageContent(data=b"\\x89PNG...", media_type="image/png")
    """

    type: Literal["image"] = "image"
    url: Optional[str] = None
    file_id: Optional[str] = None
    data: Optional[bytes] = None
    media_type: str = "image/jpeg"
    detail: Literal["low", "high", "auto"] = "auto"

    model_config = {"frozen": False}

    @model_validator(mode="after")
    def _check_exactly_one_source(self) -> "ImageContent":
        sources = sum(x is not None for x in (self.url, self.file_id, self.data))
        if sources != 1:
            raise ValueError("Exactly one of url, file_id, or data must be provided")
        return self

    def to_data_url(self) -> str:
        """Encode raw bytes as a base64 data URL."""
        if self.data is None:
            raise ValueError("to_data_url() requires data to be set")
        b64 = base64.b64encode(self.data).decode("utf-8")
        return f"data:{self.media_type};base64,{b64}"


class AudioContent(BaseModel):
    """Audio input — raw bytes with format metadata.

    Example::

        AudioContent(data=b"...", format="mp3")
    """

    type: Literal["audio"] = "audio"
    data: bytes
    format: Literal["mp3", "wav", "opus", "flac", "ogg"] = "mp3"
    transcript: Optional[str] = None

    model_config = {"frozen": True}


class VideoContent(BaseModel):
    """Video input — URL or raw bytes.

    Example::

        VideoContent(url="https://example.com/clip.mp4")
        VideoContent(data=b"...", media_type="video/mp4")
    """

    type: Literal["video"] = "video"
    url: Optional[str] = None
    data: Optional[bytes] = None
    media_type: str = "video/mp4"

    model_config = {"frozen": True}


class DocumentContent(BaseModel):
    """Document input — URL, file-ID, or raw bytes (e.g. PDF).

    Exactly one of ``url``, ``file_id``, or ``data`` must be provided.

    Examples::

        DocumentContent(url="https://example.com/invoice.pdf", media_type="application/pdf")
        DocumentContent(data=b"%PDF-1.4...", media_type="application/pdf", filename="invoice.pdf")
    """

    type: Literal["document"] = "document"
    url: Optional[str] = None
    file_id: Optional[str] = None
    data: Optional[bytes] = None
    media_type: str = "application/pdf"
    filename: Optional[str] = None

    model_config = {"frozen": False}

    @model_validator(mode="before")
    @classmethod
    def _decode_data(cls, data: Any) -> Any:
        if isinstance(data, dict) and "data" in data:
            raw_data = data["data"]
            if isinstance(raw_data, str):
                try:
                    if raw_data.startswith("data:"):
                        parts = raw_data.split(",", 1)
                        if len(parts) > 1:
                            raw_data = parts[1]
                    data["data"] = base64.b64decode(raw_data)
                except Exception:
                    pass
        return data

    @model_validator(mode="after")
    def _check_exactly_one_source(self) -> "DocumentContent":
        sources = sum(x is not None for x in (self.url, self.file_id, self.data))
        if sources != 1:
            raise ValueError("Exactly one of url, file_id, or data must be provided")
        return self


MediaContent = Union[ImageContent, AudioContent, VideoContent, DocumentContent]
"""Union of all media attachment types."""


# What message.content actually carries — text strings or typed media.
MessageContent = Union[str, ImageContent, AudioContent, VideoContent, DocumentContent]
"""A single content item in a message: plain string or typed media."""


# ── Helpers ──────────────────────────────────────────────────────────────


def content_block_from_dict(data: dict[str, object]) -> ContentBlock:
    """Convert a raw dict to the correct ContentBlock variant.

    Accepts dicts with a ``type`` field::

        content_block_from_dict({"type": "text", "text": "hello"})
        content_block_from_dict({"type": "image", "data": "...", "media_type": "image/png"})
        content_block_from_dict({"type": "audio", "data": "...", "media_type": "audio/wav"})
        content_block_from_dict({"type": "code", "code": "print('hi')", "language": "python"})
        content_block_from_dict({"type": "error", "error_type": "ValueError", "message": "bad"})
    """
    block_type = data.get("type", "text")
    if block_type == "text":
        return TextBlock(text=str(data.get("text", "")))
    if block_type == "image":
        return ImageBlock(
            data=str(data.get("data", "")),
            media_type=str(data.get("media_type", "image/png")),
        )
    if block_type == "audio":
        return AudioBlock(
            data=str(data.get("data", "")),
            media_type=str(data.get("media_type", "audio/wav")),
            transcript=data.get("transcript"),  # type: ignore[arg-type]
        )
    if block_type == "video":
        return VideoBlock(
            url=data.get("url"),  # type: ignore[arg-type]
            data=data.get("data"),  # type: ignore[arg-type]
            media_type=str(data.get("media_type", "video/mp4")),
        )
    if block_type == "document":
        return DocumentBlock(
            data=str(data.get("data", "")),
            media_type=str(data.get("media_type", "application/octet-stream")),
            filename=data.get("filename"),  # type: ignore[arg-type]
        )
    if block_type == "data":
        raw = data.get("data", {})
        return DataBlock(
            data=raw if isinstance(raw, dict) else {},  # type: ignore[arg-type]
            schema_id=data.get("schema_id"),  # type: ignore[arg-type]
        )
    if block_type == "code":
        return CodeBlock(
            code=str(data.get("code", "")),
            language=str(data.get("language", "python")),
        )
    if block_type == "error":
        raw_details = data.get("details")
        return ErrorBlock(
            error_type=str(data.get("error_type", "Error")),
            message=str(data.get("message", "")),
            details=raw_details if isinstance(raw_details, dict) else None,  # type: ignore[arg-type]
            recoverable=bool(data.get("recoverable", True)),
        )
    if block_type == "tool_use":
        return ToolUseBlock(
            tool_call_id=str(data.get("tool_call_id", "")),
            tool_name=str(data.get("tool_name", "")),
            arguments=data.get("arguments", {}),  # type: ignore[arg-type]
        )
    if block_type == "tool_result":
        raw_content = data.get("content", [])
        inner: list[ContentBlock] = []
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict):
                    inner.append(content_block_from_dict(item))
                elif isinstance(item, str):
                    inner.append(TextBlock(text=item))
                else:
                    logger.warning(
                        "Unexpected item type %s in tool_result content; skipping.",
                        type(item).__name__,
                    )
        return ToolResultBlock(
            tool_call_id=str(data.get("tool_call_id", "")),
            content=inner,
            is_error=bool(data.get("is_error", False)),
        )
    if block_type == "thinking":
        return ThinkingBlock(
            text=str(data.get("text", "")),
            redacted=bool(data.get("redacted", False)),
        )
    # Fallback: treat unknown types as text
    return TextBlock(text=str(data))


def content_blocks_to_str(blocks: list[ContentBlock]) -> str:
    """Extract a human-readable string from a list of content blocks."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ImageBlock):
            parts.append("[Image]")
        elif isinstance(block, AudioBlock):
            if block.transcript:
                parts.append(f"[Audio transcript: {block.transcript}]")
            else:
                parts.append(f"[Audio: {block.media_type}]")
        elif isinstance(block, VideoBlock):
            ref = block.url or block.media_type
            parts.append(f"[Video: {ref}]")
        elif isinstance(block, DocumentBlock):
            name = block.filename or block.media_type
            parts.append(f"[Document: {name}]")
        elif isinstance(block, DataBlock):
            import json as _json

            parts.append(_json.dumps(block.data))
        elif isinstance(block, CodeBlock):
            parts.append(f"```{block.language}\n{block.code}\n```")
        elif isinstance(block, ErrorBlock):
            parts.append(f"[{block.error_type}]: {block.message}")
        elif isinstance(block, ToolUseBlock):
            parts.append(f"[ToolCall: {block.tool_name}({block.tool_call_id})]")
        elif isinstance(block, ToolResultBlock):
            inner = content_blocks_to_str(block.content) if block.content else "(empty)"
            prefix = "[ToolError" if block.is_error else "[ToolResult"
            parts.append(f"{prefix}: {block.tool_call_id}] {inner}")
        elif isinstance(block, ThinkingBlock):
            if not block.redacted:
                parts.append(f"[Thinking] {block.text}")
            else:
                parts.append("[Thinking: redacted]")
    return "\n".join(parts)


# Rebuild ToolResultBlock to resolve the forward reference to ContentBlock.
ToolResultBlock.model_rebuild()
