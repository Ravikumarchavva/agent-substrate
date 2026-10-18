"""The document library contract: what knowledge, conversations and the `documents` tool need from a documents engine.

A *collection* is a key prefix under a tenant (`tenants/<t>/knowledge/<kb>/library`, or a conversation's documents
prefix); every method is confined to the collection it is given, and the caller builds that collection from
authenticated identity, never from a model's output.

TODO(integration): [data] adapter - `loamz.data.storage` documents (Library/Reader/OKF, embeddings, reranking) will
implement this Protocol; `LocalLibrary` in `store.py` is the SQL-and-folder implementation used until then.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from loamz.agents.features.knowledge.documents.model import (
    Added,
    Described,
    DocumentBrief,
    DocumentInfo,
    Hits,
    Listing,
    Outline,
    Passage,
    Topic,
)


@runtime_checkable
class DocumentLibrary(Protocol):
    async def add(
        self,
        data: bytes,
        filename: str,
        *,
        collection: str,
        resource: str | None = None,
        content_type: str | None = None,
        sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Added:
        """Read `data` and file it. The same file again is a no-op (`duplicate=True`). Raises `InvalidRequestError` for a file that cannot be read."""
        ...

    async def info(self, collection: str, document: str) -> DocumentInfo | None: ...

    async def list(
        self, *, collection: str, after: str | None = None, limit: int = 50
    ) -> Listing: ...

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
        self, *, collection: str, query: str, document: str | None = None, limit: int = 5
    ) -> Hits: ...

    async def browse(self, *, collection: str, topic: str = "") -> Topic | None: ...

    async def delete(self, *, collection: str, document: str) -> bool: ...

    async def collections(self, *, under: str) -> list[tuple[str, int]]:
        """`(collection, documents)` for every collection at or below the prefix `under`."""
        ...

    async def erase_under(self, prefix: str) -> int:
        """Remove every collection at or below `prefix`; returns the documents removed."""
        ...

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Remove a tenant's documents, or only those a user's conversations hold."""
        ...

    # ── enrichment ──────────────────────────────────────────────────────────
    async def brief(self, *, collection: str, document: str) -> DocumentBrief | None: ...

    async def topic_tree(self, *, collection: str) -> list[str]: ...

    async def claim(self, *, collection: str, document: str, enricher: str) -> bool:
        """Mark a document as being described; `False` when it is already running or done by this enricher."""
        ...

    async def annotate(
        self, *, collection: str, document: str, described: Described, enricher: str
    ) -> None: ...

    async def fail(self, *, collection: str, document: str, error: str) -> None: ...

    async def pending(
        self, tenant_id: str, *, enricher: str, limit: int = 50
    ) -> list[tuple[str, str]]:
        """`(collection, document)` of the tenant's documents still to describe."""
        ...


__all__ = ["DocumentLibrary"]
