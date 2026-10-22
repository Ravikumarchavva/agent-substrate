"""Document protocols — contracts for reading documents (the extractor and the OCR engine) and for filing and navigating them (the library)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from loamz.engine.documents.types import (
    Added,
    DocumentBrief,
    DocumentInfo,
    Enricher,
    EnrichmentState,
    EnrichmentUsage,
    ExtractionResult,
    Hits,
    Listing,
    OcrResult,
    Outline,
    Passage,
    Picture,
    Strategy,
    Topic,
)


@runtime_checkable
class DocumentExtractor(Protocol):
    """Contract every document reader satisfies — the built-in one, a document server, or your own.

    ``read`` never raises for a document it cannot read: it returns ``ExtractionResult(success=False, error=…)``.
    ``content_type`` is a hint (the format is sniffed from the bytes first); ``strategy`` is ``fast | auto | hi_res | ocr_only``.
    """

    async def read(
        self,
        data: bytes,
        filename: str,
        *,
        content_type: str | None = None,
        strategy: Strategy = "auto",
    ) -> ExtractionResult: ...


@runtime_checkable
class Ocr(Protocol):
    """Contract for an OCR engine: one page image in, its text out.

    ``recognize`` is synchronous (it runs in a worker, off the event loop) and must not raise on an unreadable image — return an
    empty result. Instances are rebuilt in the worker from their constructor arguments, so they must be cheap to construct.
    """

    name: str

    def recognize(self, png: bytes, *, languages: Sequence[str] = ("eng",)) -> OcrResult: ...


@runtime_checkable
class DocumentLibrary(Protocol):
    """Contract for a place documents are filed and navigated: the folder-based ``Library``, or any adapter (a governed document service)
    that answers the same calls. An agent's documents tool and the data server's routes depend on this, not on a concrete library.

    Every method takes the ``collection`` (a key prefix) it works in; a collection is whatever the caller says it is, and an implementation
    must keep one collection invisible from another. Reads return ``None`` / empty for what does not exist, never raise.

    The **enrichment surface** (the last group) is what ``loamz.engine.documents.enrichment.enrich`` / ``reorganise`` run over: the model
    call and its token budget stay with the caller (ADR 0004); the library only stores what was written and keeps the books
    (``claim``, ``fail``, ``annotate``, ``set_topics``).

    Conformance: ``loamz.engine.testing.conformance.document_library.DocumentLibraryConformance``.
    """

    @property
    def file_topics(self) -> bool:
        """Whether documents are filed in a topic tree (``browse``): knowledge bases are, a conversation's documents are not."""
        ...

    # ── reads ──────────────────────────────────────────────────────────────────────────────────────────────────────

    async def info(self, collection: str, document: str) -> DocumentInfo | None: ...

    async def list(
        self, *, collection: str, limit: int = 50, after: str | None = None
    ) -> Listing: ...

    async def collections(self, *, under: str) -> list[tuple[str, int]]: ...

    async def outline(
        self, *, collection: str, document: str, section: int | None = None
    ) -> Outline | None: ...

    async def read(
        self,
        *,
        collection: str,
        document: str,
        section: int | None = None,
        pages: tuple[int, int] | None = None,
        offset: int = 0,
    ) -> Passage | None: ...

    async def find(
        self,
        *,
        collection: str,
        query: str,
        document: str | None = None,
        limit: int = 8,
    ) -> Hits: ...

    async def view(
        self,
        *,
        collection: str,
        image: str | None = None,
        document: str | None = None,
        page: int | None = None,
    ) -> Picture | None: ...

    async def browse(self, *, collection: str, topic: str = "") -> Topic | None: ...

    # ── writes ─────────────────────────────────────────────────────────────────────────────────────────────────────

    async def add(
        self,
        source: bytes | ExtractionResult,
        filename: str = "",
        *,
        collection: str,
        resource: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        sha256: str | None = None,
    ) -> Added: ...

    async def delete(self, *, collection: str, document: str) -> bool: ...

    async def erase_under(self, prefix: str) -> int: ...

    async def reindex(self, *, collection: str, missing_only: bool = False) -> int: ...

    async def embed_missing(self, *, collection: str) -> int: ...

    # ── enrichment ─────────────────────────────────────────────────────────────────────────────────────────────────

    async def pending(
        self, *, collection: str, enricher: Enricher | None = None, limit: int = 50
    ) -> list[str]: ...

    async def pending_all(
        self,
        *,
        enricher: Enricher | None = None,
        limit: int = 100,
        matching: str = "%",
        excluding: str | None = None,
    ) -> list[tuple[str, str]]: ...

    async def needs_reorganising(self, collection: str) -> bool: ...

    async def brief(self, *, collection: str, document: str) -> DocumentBrief | None:
        """The document as an ``Enricher`` reads it: every section with its text. ``None`` if there is no such document."""
        ...

    async def enrichment_state(self, *, collection: str, document: str) -> EnrichmentState | None:
        """Where the document's description stands, or ``None`` if it has never been claimed."""
        ...

    async def claim(
        self, *, collection: str, document: str, enricher: Enricher, attempts: int
    ) -> None:
        """Record that ``enricher`` is describing the document now (``running``); ``pending`` skips it until the claim goes stale."""
        ...

    async def fail(
        self,
        *,
        collection: str,
        document: str,
        enricher: Enricher,
        attempts: int,
        error: str,
    ) -> None:
        """Record that describing it failed (``failed``, retried by ``pending`` up to three attempts). The counted index stays."""
        ...

    async def topic_tree(self, *, collection: str) -> list[str]:
        """The filing tree so far, most-used topics first and bounded: what an ``Enricher`` is shown so it prefers an existing topic."""
        ...

    async def annotate(
        self,
        *,
        collection: str,
        document: str,
        card: str,
        sections: Mapping[int, str],
        topics: Sequence[str],
        enricher: Enricher,
        attempts: int,
        usage: EnrichmentUsage,
    ) -> None:
        """Store what an enricher wrote — the document's ``card``, a description per section position, its ``topics`` — labelled as
        machine-written, mark the document ``done``, and refresh whatever is derived from it (search, embeddings, topic pages).
        Section text is never changed. Idempotent: writing the same thing twice leaves one copy."""
        ...

    async def set_topics(
        self, *, collection: str, plan: Mapping[str, Sequence[str]], by: str
    ) -> None:
        """File each document in ``plan`` under the given topics (replacing its topics), refresh the topic pages and note the change in
        the collection's topic log under the name ``by``. Documents never move and nothing is deleted."""
        ...


__all__ = [
    "DocumentExtractor",
    "DocumentLibrary",
    "Ocr",
]
