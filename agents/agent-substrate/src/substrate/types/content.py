"""Universal multimodal content blocks and conversation messages.

Zero-boilerplate, immutable data primitives powered natively by Pydantic v2.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    model_validator,
)

from substrate.types.errors import BlockValidationError

JsonObject = dict[str, Any]


class FrozenDict(dict[str, Any]):
    """A dict that cannot be changed, and can be hashed.

    ``frozen=True`` on a pydantic model only stops attribute assignment; a dict
    or list inside it stays mutable, so a "frozen" message could be edited by
    whoever held a reference, and could not be hashed at all. Content passes
    between agents, is cached and is journaled; one consumer editing another's
    copy is exactly the bug immutability is supposed to rule out.
    """

    __slots__ = ()

    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "this mapping is immutable; copy it with dict(...) to change it"
        )

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable  # type: ignore[assignment]
    __ior__ = _immutable  # type: ignore[assignment]

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(frozenset(self.items()))

    def __reduce__(self) -> tuple[Any, ...]:
        # Rebuild from a plain dict: the default dict-subclass protocol sets
        # items one by one, which an immutable mapping refuses.
        return (FrozenDict, (dict(self),))


class FrozenList(tuple[Any, ...]):
    """An immutable sequence that still compares equal to the list it came from.

    JSON has lists, not tuples; a tool argument ``{"ids": [1, 2]}`` should equal
    ``{"ids": [1, 2]}`` after it has been frozen, or every comparison against a
    literal has to change.
    """

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return tuple.__eq__(self, tuple(other))
        return tuple.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = tuple.__hash__

    def __reduce__(self) -> tuple[Any, ...]:
        return (FrozenList, (tuple(self),))


def freeze(value: Any) -> Any:
    """Recursively turn dicts into ``FrozenDict`` and lists into ``FrozenList``."""
    if isinstance(value, dict):
        return FrozenDict({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return FrozenList(freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(v) for v in value)
    return value


FrozenJson = Annotated[dict[str, Any], AfterValidator(freeze)]
"""A JSON object that is deeply immutable once validated."""


def _json_default(value: object) -> str:
    """What ``str(block)`` shows for values JSON cannot encode. Rendering a block
    into a prompt must never raise."""
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return str(sorted(map(str, value)))
    return str(value)


class Role(StrEnum):
    """Standard conversation turn roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class KernelModel(BaseModel):
    """Base model for all kernel content structures — immutable with standard string representation."""

    model_config = ConfigDict(frozen=True)


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
    data: FrozenJson
    schema_id: str | None = None

    def __str__(self) -> str:
        return json.dumps(self.data, default=_json_default)


class ErrorBlock(KernelModel):
    """Typed error block for structured failure reporting."""

    type: Literal["error"] = "error"
    error_type: str
    message: str
    details: FrozenJson | None = None
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
        return cls(
            type="image",
            url=url,
            data=data,
            file_id=file_id,
            media_type=media_type,
            **kwargs,
        )

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
        return cls(
            type="audio",
            url=url,
            data=data,
            file_id=file_id,
            media_type=media_type,
            **kwargs,
        )

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
        return cls(
            type="video",
            url=url,
            data=data,
            file_id=file_id,
            media_type=media_type,
            **kwargs,
        )

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
        return cls(
            type="document",
            url=url,
            data=data,
            file_id=file_id,
            media_type=media_type,
            **kwargs,
        )

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
    arguments: FrozenJson = Field(default_factory=FrozenDict)
    # Set by an LLM client when the model's arguments weren't valid JSON —
    # the harness reports it back to the model instead of running the tool.
    arguments_error: str | None = None

    def __str__(self) -> str:
        return f"[ToolCall: {self.tool_name}({self.call_id})]"


class UnknownBlock(KernelModel):
    """Lossless carrier for forward-compatibility with future provider types."""

    type: Literal["unknown"] = "unknown"
    raw: FrozenJson = Field(default_factory=FrozenDict)

    def __str__(self) -> str:
        return f"[UnknownBlock: {self.raw.get('type', '?')}]"


def _coerce_blocks(v: Any, info: ValidationInfo) -> Any:
    # Persisted and wire data (JSON) may carry block types a newer version
    # wrote; code constructing blocks in Python may not invent them.
    forward_compatible = info.mode == "json" or bool(
        (info.context or {}).get("forward_compatible")
    )
    if isinstance(v, str):
        return (TextBlock(text=v),)
    if isinstance(v, (BaseModel, dict)):
        v = [v]
    if isinstance(v, (list, tuple)):
        return tuple(
            parse_content_block(item, forward_compatible=forward_compatible)
            if isinstance(item, dict)
            else item
            for item in v
        )
    return v


BlockList = Annotated[tuple["ContentBlock", ...], BeforeValidator(_coerce_blocks)]


class ToolResultBlock(KernelModel):
    """Multimodal tool execution outcome."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str = ""
    content: BlockList = Field(default_factory=tuple)
    is_error: bool = False

    @property
    def text(self) -> str:
        """Concatenated plain text of all TextBlock items."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def __str__(self) -> str:
        inner = "\n".join(str(b) for b in self.content)
        prefix = "[ToolError" if self.is_error else "[ToolResult"
        return (
            f"{prefix}: {self.call_id}] {inner}"
            if inner
            else f"{prefix}: {self.call_id}] (empty)"
        )


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


def parse_content_block(data: Any, *, forward_compatible: bool = False) -> ContentBlock:
    """The single way to parse a dict or object into a ``ContentBlock``.

    ``UnknownBlock`` carries a block type this version does not know, so data
    written by a newer version can still be read. That is a property of
    *persisted or wire* data. Everywhere else a block with no ``type``, or a
    type that is not one of ours, is a bug — a typo silently becoming an
    ``UnknownBlock`` that every encoder then drops is lost content — so it
    raises unless the caller says it is reading data that may come from the
    future (``forward_compatible=True``).
    """
    if isinstance(data, dict):
        tag = data.get("type")
        if tag is None:
            raise BlockValidationError(f"content block has no 'type': {sorted(data)}")
        if tag not in _KNOWN_BLOCK_TAGS:
            if forward_compatible:
                return UnknownBlock(raw=dict(data))
            raise BlockValidationError(
                f"unknown content block type {tag!r}; known: {sorted(_KNOWN_BLOCK_TAGS)}"
            )
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
    content: BlockList = Field(default_factory=tuple)
    name: str | None = None
    metadata: FrozenJson = Field(default_factory=FrozenDict)

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
