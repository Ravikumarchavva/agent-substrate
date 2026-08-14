"""RagBackend — the contract every RAG backend (local or managed) implements.

Deliberately coarse: one Protocol with ``ingest``/``query``, not separately
swappable loader/embedder/store/reranker pieces. A managed service like
Pinecone Assistant does parsing, chunking, embedding, storage, and retrieval
as one opaque call — it has no seam to plug in at the sub-component level, so
forcing a layered design would mean maintaining two incompatible shapes at
once. Swap the whole backend; that's the granularity every option here
actually supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from substrate.kernel.storage.vector import SearchResult


@dataclass(slots=True)
class IngestResult:
    """Outcome of one ``ingest()`` call.

    ``chunks_indexed`` is ``-1`` when the backend doesn't report a chunk
    count (Pinecone Assistant manages chunking internally and never
    surfaces it) — callers that need an exact count should check for that
    sentinel rather than assume it's always meaningful.
    """

    chunks_indexed: int
    document_id: str | None = None


class RagBackendUnavailableError(RuntimeError):
    """Raised by the factory for an unknown backend name, or one whose
    prerequisites are missing (e.g. no API key) — fail loudly at
    construction time rather than at the first real call."""


@runtime_checkable
class RagBackend(Protocol):
    """A document-ingestion + retrieval backend."""

    name: str

    async def ingest(
        self,
        source: str | bytes | Path,
        *,
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult: ...

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """``filter`` restricts results by metadata equality (e.g.
        ``{"file_id": ..., "page_number": 13}`` for explicit page
        navigation) — currently only honored by ``LocalRagBackend``;
        ``PineconeRagBackend`` accepts and ignores it (Assistant has its own
        opaque filtering, no generic metadata-equality seam)."""
        ...

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
    ) -> str:
        """Retrieve and generate an answer in one call."""
        ...


__all__ = ["IngestResult", "RagBackend", "RagBackendUnavailableError"]
