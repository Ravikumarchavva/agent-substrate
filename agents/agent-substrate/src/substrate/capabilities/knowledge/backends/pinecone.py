"""PineconeRagBackend — wraps Pinecone Assistant.

Pinecone Assistant handles parsing, chunking, embedding, storage, and
retrieval as one opaque service — there is no seam to plug in at the
loader/embedder/store level, which is exactly why ``RagBackend`` is a coarse
Protocol rather than a layered one (see ``base.py``'s docstring).

Requires the optional ``pinecone`` extra (``uv sync --extra rag-pinecone``);
imported lazily in ``__init__`` so it's never a hard dependency for
deployments using only ``LocalRagBackend``.

NOTE: call shapes here follow Pinecone's public Assistant API docs as of
this writing (``Pinecone(api_key=...).assistant.Assistant(name)``,
``.upload_file``, ``.context``, ``.chat``) — the SDK isn't installed in this
environment to verify against, so treat this as needing a real smoke test
against a live API key (see the plan's Manual E2E step) before trusting it
in production; the SDK's exact method/kwarg names may have shifted.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import SearchResult

from .base import IngestResult, RagBackendUnavailableError


def _snippet_file_id(snippet: Any) -> str:
    reference = getattr(snippet, "reference", None)
    file = getattr(reference, "file", None)
    return getattr(file, "id", "") or ""


class PineconeRagBackend:
    """RAG via Pinecone Assistant — parsing, embedding, and storage are all
    handled server-side; this class only adapts the wire shapes."""

    name = "pinecone"

    def __init__(self, *, api_key: str, assistant_name: str) -> None:
        try:
            from pinecone import Pinecone  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RagBackendUnavailableError(
                "PineconeRagBackend requires the 'pinecone' package. "
                "Install with: uv sync --extra rag-pinecone"
            ) from exc
        if not api_key:
            raise RagBackendUnavailableError(
                "PineconeRagBackend requires an api_key (or PINECONE_API_KEY)."
            )
        self._client = Pinecone(api_key=api_key)
        self._assistant = self._client.assistant.Assistant(
            assistant_name=assistant_name
        )

    async def ingest(
        self,
        source: str | bytes | Path,
        *,
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """``collection`` is accepted for Protocol compatibility but ignored:
        Pinecone Assistant has no sub-collection concept — partition via
        ``metadata`` instead, which is preserved and filterable at query time."""
        if isinstance(source, bytes):
            # upload_file wants a path; bytes (e.g. an in-memory chat
            # upload) get a throwaway temp file for the duration of the call.
            with tempfile.NamedTemporaryFile(
                suffix=Path(metadata.get("filename", "") if metadata else "").suffix
            ) as tmp:
                tmp.write(source)
                tmp.flush()
                file_ref = await asyncio.to_thread(
                    self._assistant.upload_file, file_path=tmp.name, metadata=metadata
                )
        else:
            file_ref = await asyncio.to_thread(
                self._assistant.upload_file, file_path=str(source), metadata=metadata
            )
        return IngestResult(
            chunks_indexed=-1, document_id=getattr(file_ref, "id", None)
        )

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
    ) -> list[SearchResult]:
        response = await asyncio.to_thread(
            self._assistant.context, query=question, top_k=limit
        )
        return [
            SearchResult(
                id=_snippet_file_id(snippet),
                content=[TextBlock(text=snippet.text)],
                score=getattr(snippet, "score", 0.0),
                metadata={},
            )
            for snippet in getattr(response, "snippets", [])
        ]

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
    ) -> str:
        response = await asyncio.to_thread(
            self._assistant.chat, messages=[{"role": "user", "content": question}]
        )
        return response.message.content


__all__ = ["PineconeRagBackend"]
