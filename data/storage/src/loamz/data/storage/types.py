"""The content values storage keeps: immutable models, text and media blocks, and UUIDv7 ids.

Memory records and vector documents hold content as blocks. Storage needs only two kinds, text and media; a richer
block vocabulary (tool calls, reasoning, …) belongs to whoever builds conversations on top, and converts to these when
it stores something.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

JsonObject = dict[str, Any]


class KernelModel(BaseModel):
    """Base of every stored value: immutable once built."""

    model_config = ConfigDict(frozen=True)


class TextBlock(KernelModel):
    """Plain text or markdown."""

    type: Literal["text"] = "text"
    text: str

    def __str__(self) -> str:
        return self.text


class MediaBlock(KernelModel):
    """Binary media (an image, audio, video or document) given by exactly one of a URL, inline bytes or a file id."""

    type: Literal["image", "audio", "video", "document"]
    media_type: str = "application/octet-stream"
    url: str | None = None
    data: bytes | None = None  # base64 in JSON
    file_id: str | None = None
    detail: Literal["low", "high", "auto"] = "auto"
    filename: str | None = None
    transcript: str | None = None
    storage_key: str | None = None

    model_config = ConfigDict(frozen=True, ser_json_bytes="base64", val_json_bytes="base64")

    @model_validator(mode="after")
    def _one_source(self) -> Self:
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
            type="image", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs
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
            type="audio", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs
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
            type="video", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs
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
            type="document", url=url, data=data, file_id=file_id, media_type=media_type, **kwargs
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


class DataBlock(KernelModel):
    """Structured JSON payload for rich tool results or artifacts."""

    type: Literal["data"] = "data"
    data: JsonObject
    schema_id: str | None = None

    def __str__(self) -> str:
        import json

        return json.dumps(self.data)


ContentBlock = Annotated[TextBlock | MediaBlock | DataBlock, Field(discriminator="type")]


def _coerce_blocks(value: Any) -> Any:
    if isinstance(value, str):
        return (TextBlock(text=value),)
    if isinstance(value, (BaseModel, dict)):
        return (value,)
    return value


BlockList = Annotated[tuple[ContentBlock, ...], BeforeValidator(_coerce_blocks)]
"""Blocks as a tuple; a bare string becomes one text block."""


def content_blocks_to_str(blocks: Sequence[ContentBlock]) -> str:
    return "\n".join(str(b) for b in blocks)


_lock = threading.Lock()
_last_ms = -1
_last_counter = 0


def new_id() -> str:
    """A fresh UUIDv7 as 32 lower-case hex characters, strictly increasing within a process."""
    global _last_ms, _last_counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms > _last_ms:
            _last_ms = now_ms
            _last_counter = secrets.randbits(11)
        else:
            _last_counter += 1
            if _last_counter >= 1 << 12:
                _last_ms += 1
                _last_counter = 0
        ms, counter = _last_ms, _last_counter
    value = (
        (ms & 0xFFFF_FFFF_FFFF) << 80
        | 0x7 << 76
        | (counter & 0xFFF) << 64
        | 0b10 << 62
        | secrets.randbits(62)
    )
    return f"{value:032x}"


__all__ = [
    "BlockList",
    "ContentBlock",
    "DataBlock",
    "JsonObject",
    "KernelModel",
    "MediaBlock",
    "TextBlock",
    "content_blocks_to_str",
    "new_id",
]
