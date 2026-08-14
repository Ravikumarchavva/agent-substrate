"""PineconeRagBackend — wraps Pinecone Assistant.

Pinecone Assistant handles parsing, chunking, embedding, storage, and
retrieval as one opaque service — there is no seam to plug in at the
loader/embedder/store level, which is exactly why ``RagBackend`` is a coarse
Protocol rather than a layered one (see ``base.py``'s docstring).

Requires the optional ``pinecone`` extra (``uv sync --extra rag-pinecone``);
imported lazily in ``__init__`` so it's never a hard dependency for
deployments using only ``LocalRagBackend``.

Call shapes below are verified against the installed ``pinecone`` SDK
(v9.1.0, ``pc.assistants.create/describe`` + the returned ``AssistantModel``'s
``.upload_file``/``.context``/``.chat`` — NOT the older ``pc.assistant.Assistant(...)``
shape some docs/examples still show) — see
``tests/capabilities/test_rag_backends.py`` for a live-shape smoke test.

One Pinecone Assistant is shared across every ``collection`` (chat thread /
knowledge base) — Assistant itself has no sub-collection concept — so
isolation between collections is enforced via a ``collection`` metadata tag
on every uploaded file plus a matching ``filter`` on every query. Without
this, one thread's uploaded docs would be retrievable from every other
thread sharing the same assistant.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import SearchResult

from .base import IngestResult, RagBackendUnavailableError


def _citation_metadata(snippet: Any) -> dict[str, Any]:
    """Flatten ``snippet.reference`` into the citation vocabulary
    ``capabilities/knowledge/citations.py`` expects: ``filename``, ``pages``,
    ``page_number`` (the jump target — the first page of the chunk's span),
    plus whatever custom metadata was set at ingest (``file_id``,
    ``session_path``, ...), echoed back unchanged on ``reference.file.metadata``.

    Fully defensive: a snippet whose ``reference`` is missing or reshaped by a
    future SDK version must degrade to ``{}``, never raise — a citation is a
    nice-to-have on top of a working answer, not something that should be able
    to break retrieval.
    """
    reference = getattr(snippet, "reference", None)
    if reference is None:
        return {}
    file = getattr(reference, "file", None)
    raw_metadata = getattr(file, "metadata", None)
    file_metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    raw_pages = getattr(reference, "pages", None)
    pages: list[int] = []
    if isinstance(raw_pages, (list, tuple)):
        for value in raw_pages:
            try:
                pages.append(int(value))
            except (TypeError, ValueError):
                continue
    return {
        **file_metadata,
        "filename": file_metadata.get("filename") or getattr(file, "name", ""),
        "pages": pages,
        "page_number": pages[0] if pages else None,
        "pinecone_file_id": getattr(file, "id", ""),
    }


class PineconeRagBackend:
    """RAG via Pinecone Assistant — parsing, embedding, and storage are all
    handled server-side; this class only adapts the wire shapes."""

    name = "pinecone"

    def __init__(self, *, api_key: str, assistant_name: str) -> None:
        try:
            from pinecone import Pinecone  # type: ignore[import-not-found]
            from pinecone.exceptions import (  # type: ignore[import-not-found]
                NotFoundException,
            )
        except ImportError as exc:
            raise RagBackendUnavailableError(
                "PineconeRagBackend requires the 'pinecone' package. "
                "Install with: uv sync --extra rag-pinecone"
            ) from exc
        if not api_key:
            raise RagBackendUnavailableError(
                "PineconeRagBackend requires an api_key (or PINECONE_API_KEY)."
            )
        if not assistant_name:
            raise RagBackendUnavailableError(
                "PineconeRagBackend requires an assistant_name."
            )
        client = Pinecone(api_key=api_key)
        try:
            self._assistant = client.assistants.describe(name=assistant_name)
        except NotFoundException:
            self._assistant = client.assistants.create(name=assistant_name)

    async def ingest(
        self,
        source: str | bytes | Path,
        *,
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """``collection`` tags the uploaded file's metadata (``query``/
        ``query_with_context`` filter on it) — Pinecone Assistant has no
        native sub-collection concept, so this is how isolation between
        collections is actually enforced.

        ``multimodal=True`` is always passed to ``upload_file`` — without it,
        Pinecone's parser only picks up text that's already a real text
        layer in the PDF; on a scanned document (a photo/scan of a printed
        letter with no embedded text) that means just the handful of lines
        that happen to be real text (e.g. typed page captions) while the
        actual letter body — a scanned image — is silently dropped. With
        multimodal parsing Pinecone OCRs/reads the page images too. Verified
        live: without it, a scanned NAAC letter yielded only page-caption
        text ("Academic Year: 2012-2013..."); with it, the full letter
        bodies (names, dates, signatures) came back in context()."""
        file_metadata = {**(metadata or {}), "collection": collection}
        if isinstance(source, bytes):
            # upload_file wants a path; bytes (e.g. an in-memory chat
            # upload) get a throwaway temp file for the duration of the call.
            with tempfile.NamedTemporaryFile(
                suffix=Path(metadata.get("filename", "") if metadata else "").suffix
            ) as tmp:
                tmp.write(source)
                tmp.flush()
                file_ref = await asyncio.to_thread(
                    self._assistant.upload_file,
                    file_path=tmp.name,
                    metadata=file_metadata,
                    multimodal=True,
                )
        else:
            file_ref = await asyncio.to_thread(
                self._assistant.upload_file,
                file_path=str(source),
                metadata=file_metadata,
                multimodal=True,
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
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        # `filter` (generic metadata-equality dict, e.g. page navigation) has
        # no Pinecone Assistant equivalent to map onto — ignored here, same
        # as base.py's Protocol docstring says. Only `collection` isolation
        # is enforced, as before.
        response = await asyncio.to_thread(
            self._assistant.context,
            query=question,
            top_k=limit,
            filter={"collection": {"$eq": collection}},
        )
        results = []
        for snippet in response.snippets:
            # `snippet.content` is `str` for a text snippet, or a list of
            # content blocks for a multimodal one — we only ever ingest text.
            content = snippet.content
            text = content if isinstance(content, str) else str(content)
            # Same defensiveness as _citation_metadata: a snippet with no
            # `reference` must produce a usable (if unidentified) result, not
            # crash the whole query over one malformed entry.
            file_id = getattr(getattr(snippet, "reference", None), "file", None)
            results.append(
                SearchResult(
                    id=getattr(file_id, "id", "") or "",
                    content=[TextBlock(text=text)],
                    score=snippet.score,
                    metadata=_citation_metadata(snippet),
                )
            )
        return results

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
    ) -> str:
        response = await asyncio.to_thread(
            self._assistant.chat,
            messages=[{"role": "user", "content": question}],
            filter={"collection": {"$eq": collection}},
            stream=False,
        )
        # stream=False always returns ChatResponse, never ChatStream — the
        # SDK's return type isn't narrowed by the literal, so assert it here.
        assert hasattr(response, "message"), "unexpected streaming response"
        return response.message.content  # type: ignore[union-attr]


__all__ = ["PineconeRagBackend"]
