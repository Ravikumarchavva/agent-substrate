"""RagBackend — the contract every RAG backend (local or managed) implements.

Deliberately coarse: one Protocol with ``ingest``/``query``, not separately
swappable loader/embedder/store/reranker pieces. A managed RAG service
typically does parsing, chunking, embedding, storage, and retrieval as one
opaque call — it has no seam to plug in at the sub-component level, so
forcing a layered design would mean maintaining two incompatible shapes at
once. Swap the whole backend; that's the granularity every option here
actually supports.

``LocalRagBackend`` is the only backend today (a managed-service backend,
Pinecone Assistant, existed briefly but was removed as dead weight — never
the standard path). The Protocol stays this coarse regardless, since it's
the right shape for whatever backend comes next, not just the one that's
gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from substrate.kernel.storage.vector import SearchResult


@dataclass(slots=True)
class IngestResult:
    """Outcome of one ``ingest()`` call.

    ``chunks_indexed`` is ``-1`` when a backend doesn't report a chunk
    count (a managed backend that chunks internally and never surfaces it,
    for example) — callers that need an exact count should check for that
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
        navigation) — honored by ``LocalRagBackend``, the only backend
        today; a future opaque managed backend might accept and ignore it
        instead (no generic metadata-equality seam of its own)."""
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
