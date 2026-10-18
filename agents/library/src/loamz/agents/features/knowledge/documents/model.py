"""What the document library returns: plain frozen value types, the shape the `documents` tool and the REST layer read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MAX_READ_CHARS = 24_000
MAX_OUTLINE = 60
MAX_LIST = 50


@dataclass(frozen=True)
class Added:
    document: str
    title: str
    filename: str
    pages: int
    sections: int
    images: int = 0
    duplicate: bool = False
    """The same file was already in the collection: nothing was written."""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentInfo:
    document: str
    title: str
    filename: str
    pages: int
    sections: int
    images: int = 0
    needs_ocr: tuple[int, ...] = ()
    engine: str = "text"
    resource: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    description: str | None = None
    """A model-written card (`enrichment`), or `None` while the document only has its counted index."""
    topics: tuple[str, ...] = ()
    enrichment: str = "none"
    """`none`, `running`, `done` or `failed`."""


@dataclass(frozen=True)
class SectionInfo:
    position: int
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    tokens: int
    headings: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class Outline:
    document: DocumentInfo
    sections: tuple[SectionInfo, ...]
    more: int = 0
    headings: tuple[tuple[int, str, int], ...] = ()


@dataclass(frozen=True)
class Passage:
    document: DocumentInfo
    section: SectionInfo
    text: str
    offset: int = 0
    next_offset: int | None = None
    total_chars: int = 0


@dataclass(frozen=True)
class Hit:
    document: DocumentInfo
    section: SectionInfo
    snippet: str
    score: float
    image: str | None = None


class Hits(list[Hit]):
    """The hits of a `find`, best first; `note` is what the model should be told about how they were found."""

    note: str | None = None


@dataclass(frozen=True)
class Listing:
    documents: tuple[DocumentInfo, ...]
    next: str | None = None


@dataclass(frozen=True)
class Topic:
    path: str
    children: tuple[tuple[str, int], ...]
    documents: tuple[DocumentInfo, ...]
    more: int = 0


@dataclass(frozen=True)
class SectionBrief:
    position: int
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    tokens: int
    text: str


@dataclass(frozen=True)
class DocumentBrief:
    """A document as an enricher reads it. Untrusted: it is whatever the uploader wrote."""

    document: str
    title: str
    filename: str
    pages: int
    sections: tuple[SectionBrief, ...]


@dataclass(frozen=True)
class Described:
    """What an enricher returns: a description per section position, a card, and up to two topic paths."""

    sections: Mapping[int, str]
    card: str
    topics: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


__all__ = [
    "MAX_LIST",
    "MAX_OUTLINE",
    "MAX_READ_CHARS",
    "Added",
    "Described",
    "DocumentBrief",
    "DocumentInfo",
    "Hit",
    "Hits",
    "Listing",
    "Outline",
    "Passage",
    "SectionBrief",
    "SectionInfo",
    "Topic",
]
