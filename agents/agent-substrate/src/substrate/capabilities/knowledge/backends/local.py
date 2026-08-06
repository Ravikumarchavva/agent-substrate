"""LocalRagBackend — a thin façade over the existing, working local pipeline.

No new ingestion logic. Reuses, unchanged:
  * ``DoclingClient`` → the remote docling service when ``DOCLING_SERVICE_URL``
    is configured (structure-aware: tables, layout, OCR, DOCX/PPTX) — the same
    call ``routes/chat_context.py::_extract_document_text`` makes for chat
    attachments, so parsing behavior is identical between the two paths.
  * ``PDFLoader``/``TextLoader``/``CSVLoader``/``JSONLoader`` — the local,
    no-GPU fallback (PDF/text/csv/json only; DOCX/PPTX need the docling
    service, same limitation chat attachments already have).
  * ``RAGPipeline`` — chunk → embed → store.
  * ``LLMReranker`` — optional post-query reordering.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from substrate.kernel.storage.vector import Document, SearchResult

from .base import IngestResult

if TYPE_CHECKING:
    from substrate.capabilities.knowledge.pipeline import RAGPipeline
    from substrate.capabilities.knowledge.reranker import LLMReranker
    from substrate.kernel.llm import LLMClient
    from substrate.kernel.storage.vector import VectorStore

# Extensions the local (no-docling-service) fallback can read at all.
_LOCAL_FALLBACK_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".json"}
# Requires the docling service (DOCLING_SERVICE_URL) — no local fallback,
# same limitation as chat.py's attachment handling.
_DOCLING_ONLY_EXTENSIONS = {".docx", ".pptx"}


class LocalRagBackend:
    """Self-hosted RAG: docling-or-pypdf parsing, pgvector storage."""

    name = "local"

    def __init__(
        self,
        pipeline: "RAGPipeline",
        *,
        vector_store: "VectorStore | None" = None,
        docling_service_url: str = "",
        docling_auth_token: str = "",
        docling_timeout_s: int = 90,
        reranker: "LLMReranker | None" = None,
        model_client: "LLMClient | None" = None,
    ) -> None:
        self._pipeline = pipeline
        self._vector_store = vector_store
        self._docling_url = docling_service_url
        self._docling_auth_token = docling_auth_token
        self._docling_timeout_s = docling_timeout_s
        self._reranker = reranker
        self._model_client = model_client

    async def ingest(
        self,
        source: str | bytes | Path,
        *,
        collection: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """Ingest a file path, or raw ``bytes`` with ``metadata["filename"]``
        set (needed to pick a loader/content-type for bytes — there's no
        extension to sniff otherwise)."""
        documents = await self._load(source, metadata=metadata or {})
        n = await self._pipeline.ingest_documents(documents, collection=collection)
        return IngestResult(chunks_indexed=n)

    async def query(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
    ) -> list[SearchResult]:
        fetch_limit = limit * 3 if self._reranker else limit
        results = await self._pipeline.query(
            question, collection=collection, limit=fetch_limit
        )
        if self._reranker:
            results = await self._reranker.rerank(question, results, top_k=limit)
        return results

    async def query_with_context(
        self,
        question: str,
        *,
        collection: str = "default",
        limit: int = 5,
    ) -> str:
        if self._model_client is None:
            raise RuntimeError(
                "LocalRagBackend.query_with_context requires a model_client "
                "(pass one to the constructor)."
            )
        return await self._pipeline.query_with_context(
            question,
            collection=collection,
            model_client=self._model_client,
            limit=limit,
        )

    # ── local-only management (not part of RagBackend — no collection concept
    # in Pinecone Assistant; routes/rag.py checks `backend.name == "local"`
    # before calling these) ─────────────────────────────────────────────────

    async def list_collections(self) -> list[str]:
        if self._vector_store is None:
            return []
        return await self._vector_store.list_collections()

    async def delete_collection(self, collection: str) -> int:
        if self._vector_store is None:
            return 0
        return await self._vector_store.delete_collection(collection)

    # ── internals ────────────────────────────────────────────────────────────

    async def _load(
        self, source: str | bytes | Path, *, metadata: dict[str, Any]
    ) -> list[Document]:
        name = metadata.get("filename") or (
            str(source) if isinstance(source, (str, Path)) else ""
        )
        ext = Path(name).suffix.lower()

        if ext in _DOCLING_ONLY_EXTENSIONS or (
            self._docling_url and ext not in _LOCAL_FALLBACK_EXTENSIONS
        ):
            docs = await self._load_via_docling_service(source, name, metadata)
            if docs is not None:
                return docs
            if ext in _DOCLING_ONLY_EXTENSIONS:
                raise RagLoadError(
                    f"{ext} requires DOCLING_SERVICE_URL to be configured "
                    "(no local fallback can parse it)."
                )

        return await self._load_via_registry(source, name, ext, metadata)

    async def _load_via_docling_service(
        self, source: str | bytes | Path, name: str, metadata: dict[str, Any]
    ) -> list[Document] | None:
        if not self._docling_url:
            return None
        from substrate.capabilities.knowledge.docling_client import DoclingClient

        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        content_type = metadata.get("content_type", "application/octet-stream")
        client = DoclingClient(
            base_url=self._docling_url,
            auth_token=self._docling_auth_token,
            timeout_s=self._docling_timeout_s,
        )
        try:
            result = await client.extract(data, name, content_type)
        finally:
            await client.close()
        if not result.success or not result.text.strip():
            return None
        return [
            Document.from_text(result.text, metadata={**metadata, "engine": "docling"})
        ]

    async def _load_via_registry(
        self,
        source: str | bytes | Path,
        name: str,
        ext: str,
        metadata: dict[str, Any],
    ) -> list[Document]:
        from substrate.capabilities.knowledge.loaders import (
            CSVLoader,
            DocumentLoaderRegistry,
            JSONLoader,
            PDFLoader,
            TextLoader,
        )

        registry = DocumentLoaderRegistry()
        registry.register(".pdf", PDFLoader())
        for text_ext in (".txt", ".md"):
            registry.register(text_ext, TextLoader())
        registry.register(".csv", CSVLoader())
        registry.register(".json", JSONLoader())

        try:
            # get_loader(name) — not source — since source may be bytes with
            # no extension of its own; `name` (a path or metadata["filename"])
            # is what carries it.
            loader = registry.get_loader(name)
        except ValueError as exc:
            raise RagLoadError(
                f"No local loader for {ext!r} and DOCLING_SERVICE_URL is not "
                "configured. Supported without docling: "
                f"{sorted(_LOCAL_FALLBACK_EXTENSIONS)}."
            ) from exc
        return await loader.load(source, metadata=metadata)


class RagLoadError(RuntimeError):
    """Raised when no loader (local or docling-service) can handle a source."""


__all__ = ["LocalRagBackend", "RagLoadError"]
