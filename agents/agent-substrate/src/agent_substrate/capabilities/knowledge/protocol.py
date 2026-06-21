"""RAG Provider protocol."""

from __future__ import annotations

from typing import Any, Protocol
from agent_substrate.kernel.storage.vector import SearchResult


class RAGProvider(Protocol):
    """Unified Protocol for all Retrieval-Augmented Generation pipeline types."""

    async def ingest(
        self,
        content: str | list[str],
        *,
        collection: str = "default",
        **kwargs: Any,
    ) -> int:
        """Chunk/process, embed, and store content in the retrieval backend.

        Returns:
            Number of chunks/pages/elements stored.
        """
        ...

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Query the index and return retrieved SearchResults."""
        ...

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
        **kwargs: Any,
    ) -> str:
        """Query the index, construct a prompt with the retrieved context, and generate an answer."""
        ...
