"""LocalRagBackend — a thin façade over the existing, working local pipeline.

Reuses, unchanged:
  * ``ExtractionClient`` → the remote extraction service when
    ``extraction_service_url`` is configured (layout-aware: chart/table
    detection, OCR, multimodal embedding, reranking) — the same call
    ``routes/chat_context.py::_extract_document_text`` makes for chat
    attachments, so parsing behavior is identical between the two paths.
  * ``PDFLoader``/``TextLoader``/``CSVLoader``/``JSONLoader`` — the local,
    no-service fallback (PDF/text/csv/json only; DOCX/PPTX have no
    extraction path at all — the extraction service doesn't parse them
    either, same limitation chat attachments already have).
  * ``RAGPipeline`` — chunk → embed → store (text only).
  * ``LLMReranker``/``CrossEncoderReranker`` — optional post-query reordering.

Chart/table images extracted by the service bypass ``RAGPipeline`` entirely
(it always re-embeds via the text embedding client, ignoring any pre-set
``Document.embedding`` — see ``pipeline.py::ingest_documents``) and go
through a separate multimodal ingest/query path into ``image_store``, a
second ``VectorStore`` with its own embedding dimensionality (SigLIP-base:
768) that can't share a table with the text store's dimensionality.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.kernel.storage.vector import Document, SearchResult
from substrate.logger import setup_logging

from .base import IngestResult

logger = setup_logging("substrate.knowledge.local")

if TYPE_CHECKING:
    from substrate.capabilities.knowledge.extraction_client import ExtractionClient
    from substrate.capabilities.knowledge.pipeline import RAGPipeline
    from substrate.kernel.llm import LLMClient
    from substrate.kernel.storage.vector import VectorStore

# Extensions the local (no-extraction-service) fallback can read at all.
_LOCAL_FALLBACK_EXTENSIONS = {".pdf", ".txt", ".md", ".csv", ".json"}


class LocalRagBackend:
    """Self-hosted RAG: extraction-service-or-pypdf parsing, pgvector storage."""

    name = "local"

    def __init__(
        self,
        pipeline: "RAGPipeline",
        *,
        vector_store: "VectorStore | None" = None,
        image_store: "VectorStore | None" = None,
        extraction_service_url: str = "",
        extraction_auth_token: str = "",
        extraction_timeout_s: int = 90,
        # LLMReranker or CrossEncoderReranker — no formal Protocol exists,
        # both duck-type `async rerank(query, results, *, top_k) -> list[SearchResult]`.
        reranker: Any | None = None,
        model_client: "LLMClient | None" = None,
        # Pre-built client, e.g. shared with a CrossEncoderReranker so the
        # two don't each open a separate HTTP connection to the same
        # service — see backends/factory.py. Lazily constructed from
        # extraction_service_url when not given.
        extraction_client: "ExtractionClient | None" = None,
        # Duck-typed file store (WorkspaceFileStore/S3FileStore). When given,
        # extracted chart/table images are written here and only a storage key
        # is kept in the vector row — see _ingest_images.
        file_store: Any | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._vector_store = vector_store
        self._image_store = image_store
        self._extraction_url = extraction_service_url
        self._extraction_auth_token = extraction_auth_token
        self._extraction_timeout_s = extraction_timeout_s
        self._reranker = reranker
        self._model_client = model_client
        self._extraction_client = extraction_client
        self._file_store = file_store

    def _get_extraction_client(self) -> "ExtractionClient | None":
        if not self._extraction_url:
            return None
        if self._extraction_client is None:
            from substrate.capabilities.knowledge.extraction_client import (
                ExtractionClient,
            )

            self._extraction_client = ExtractionClient(
                base_url=self._extraction_url,
                auth_token=self._extraction_auth_token,
                timeout_s=self._extraction_timeout_s,
            )
        return self._extraction_client

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
        text_documents, image_items = await self._load(source, metadata=metadata or {})
        n = 0
        if text_documents:
            n = await self._pipeline.ingest_documents(
                text_documents, collection=collection
            )
        if image_items:
            await self._ingest_images(image_items, collection=collection)
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
        # Image hits are ranked by the multimodal embedding's own similarity
        # search, not the text reranker (a cross-encoder scoring the query
        # against an ImageBlock's placeholder text repr would be meaningless)
        # — appended after reranking, not merged into it.
        image_results = await self._query_images(
            question, collection=collection, limit=limit
        )
        return results + image_results

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
        deleted = 0
        if self._vector_store is not None:
            deleted += await self._vector_store.delete_collection(collection)
        if self._image_store is not None:
            deleted += await self._image_store.delete_collection(collection)
        return deleted

    async def delete_file_images(self, *, user_id: str, file_id: str) -> int:
        """Delete the stored image objects for one file.

        ``delete_collection`` only removes vector rows; without this the image
        objects behind them would be orphaned in storage forever, still counting
        against the owner's quota. Called when an upload is discarded.
        """
        if self._file_store is None or not user_id or not file_id:
            return 0
        prefix = f"users/{user_id}/rag/{file_id}/"
        try:
            entries = await self._file_store.list_user_files(user_id)
        except Exception as exc:
            logger.warning("Listing RAG images for cleanup failed: %s", exc)
            return 0
        deleted = 0
        for key, _size, _mtime in entries:
            if not key.startswith(prefix):
                continue
            try:
                await self._file_store.delete(key)
                deleted += 1
            except Exception as exc:
                logger.warning("Deleting RAG image %s failed: %s", key, exc)
        return deleted

    async def promote(self, *, file_id: str, thread_id: str) -> int:
        """Move a staged document's chunks (text + any chart/table images)
        from its temporary ``staging:{file_id}`` collection into the real
        thread collection — a cheap re-key, no re-extraction or
        re-embedding. See ``routes/files.py``'s eager-staging background
        task (writes to the staging collection at upload time) and
        ``routes/chat_context.py`` (calls this at send time).

        Only the vector rows move. Extracted image objects are keyed
        ``users/{uid}/rag/{file_id}/...`` with no collection in the path
        precisely so promotion never has to rewrite object storage — the
        ``image_key`` held by a row stays valid as it changes collection."""
        moved = 0
        staging_collection = f"staging:{file_id}"
        if self._vector_store is not None:
            moved += await self._vector_store.rename_collection(
                staging_collection, thread_id
            )
        if self._image_store is not None:
            moved += await self._image_store.rename_collection(
                staging_collection, thread_id
            )
        return moved

    # ── multimodal (chart/table image) ingest + query ──────────────────────

    async def _ingest_images(
        self, items: list[tuple[bytes, dict[str, Any]]], *, collection: str
    ) -> None:
        if self._image_store is None:
            return
        client = self._get_extraction_client()
        if client is None:
            return

        documents: list[Document] = []
        for i, (data, meta) in enumerate(items):
            vector = await client.embed_image(data)
            if vector is None:
                continue  # one bad image must not fail the whole ingest
            media_type = meta.get("media_type", "image/png")
            key = await self._store_image_bytes(data, meta, index=i)
            if key is not None:
                # Reference, not bytes: a page image is ~500KB, and inlining
                # them made the images dwarf the vectors they exist to serve
                # (5.6MB of images against 248KB of text vectors). The row keeps
                # the embedding — the only part search needs — and _query_images
                # resolves the key back to bytes on the way out.
                documents.append(
                    Document(
                        content=[TextBlock(text=f"[{meta.get('label') or 'image'}]")],
                        embedding=vector,
                        metadata={**meta, "image_key": key, "media_type": media_type},
                    )
                )
            else:
                # No file store (or the write failed): keep the old inline
                # behaviour rather than dropping the image entirely.
                documents.append(
                    Document(
                        content=[ImageBlock(data=data, media_type=media_type)],
                        embedding=vector,
                        metadata=meta,
                    )
                )
        if documents:
            await self._image_store.add(documents, collection=collection)

    async def _store_image_bytes(
        self, data: bytes, meta: dict[str, Any], *, index: int
    ) -> str | None:
        """Write one extracted image to the file store, returning its key.

        Keyed under the owning user so it is covered by the same per-user
        quota, listing and deletion as everything else they own. Returns
        ``None`` when there is nothing to write to or no owner to attribute it
        to, which the caller treats as "fall back to inlining".
        """
        if self._file_store is None:
            return None
        user_id = str(meta.get("user_id") or "")
        file_id = str(meta.get("file_id") or "")
        if not user_id or not file_id:
            return None
        media_type = str(meta.get("media_type") or "image/png")
        ext = media_type.rsplit("/", 1)[-1] or "png"
        page = meta.get("page_number")
        name = f"p{page}-{index}.{ext}" if page is not None else f"{index}.{ext}"
        key = f"users/{user_id}/rag/{file_id}/{name}"
        try:
            await self._file_store.upload(key, data, content_type=media_type)
        except Exception as exc:
            logger.warning("Storing RAG image %s failed: %s", key, exc)
            return None
        return key

    async def _query_images(
        self, question: str, *, collection: str, limit: int
    ) -> list[SearchResult]:
        if self._image_store is None:
            return []
        client = self._get_extraction_client()
        if client is None:
            return []
        query_vector = await client.embed_text(question)
        if query_vector is None:
            return []
        results = await self._image_store.search(
            query_vector, collection=collection, limit=limit
        )
        return [await self._rehydrate_image(r) for r in results]

    async def _rehydrate_image(self, result: SearchResult) -> SearchResult:
        """Swap a stored ``image_key`` back for the real pixels.

        Resolution happens here, on every read, rather than being baked into the
        stored row — so a conversation that resumes a week later (a paused HITL
        turn, a reopened thread) still gets the image, and nothing durable ever
        holds a URL that could expire. Callers upstream (``knowledge_search``,
        and through it the model) keep seeing an ordinary ``ImageBlock``.
        """
        key = (result.metadata or {}).get("image_key")
        if not key or self._file_store is None:
            return result
        try:
            data = await self._file_store.download(str(key))
        except Exception as exc:
            # Leave the placeholder text in place: a missing image should
            # degrade the answer, not fail the search.
            logger.warning("Loading RAG image %s failed: %s", key, exc)
            return result
        media_type = str((result.metadata or {}).get("media_type") or "image/png")
        return replace(
            result,
            content=[
                ImageBlock(
                    data=data,
                    media_type=media_type,
                    # Carried alongside the bytes so downstream consumers that
                    # only need a reference (the wire-event log) can link rather
                    # than inline a base64 copy. Model encoders ignore it.
                    storage_key=str(key),
                )
            ],
        )

    # ── internals ────────────────────────────────────────────────────────────

    async def _load(
        self, source: str | bytes | Path, *, metadata: dict[str, Any]
    ) -> tuple[list[Document], list[tuple[bytes, dict[str, Any]]]]:
        name = metadata.get("filename") or (
            str(source) if isinstance(source, (str, Path)) else ""
        )
        ext = Path(name).suffix.lower()

        if self._extraction_url and ext not in _LOCAL_FALLBACK_EXTENSIONS:
            result = await self._load_via_extraction_service(source, name, metadata)
            if result is not None:
                return result
            raise RagLoadError(
                f"{ext} extraction failed and no local fallback exists for it."
            )
        if ext == ".pdf" and self._extraction_url:
            result = await self._load_via_extraction_service(source, name, metadata)
            if result is not None:
                return result
            # Extraction service failed — fall through to the local pypdf
            # path below rather than losing the document entirely.

        return await self._load_via_registry(source, name, ext, metadata), []

    async def _load_via_extraction_service(
        self, source: str | bytes | Path, name: str, metadata: dict[str, Any]
    ) -> tuple[list[Document], list[tuple[bytes, dict[str, Any]]]] | None:
        if not self._extraction_url:
            return None
        client = self._get_extraction_client()
        if client is None:
            return None

        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        content_type = metadata.get("content_type", "application/octet-stream")
        result = await client.extract(data, name, content_type)
        if not result.success:
            return None

        text_documents = [
            Document.from_text(
                page.text,
                metadata={
                    **metadata,
                    "engine": result.engine,
                    "page_number": page.page_number,
                    "total_pages": result.page_count,
                },
            )
            for page in result.pages
            if page.text.strip()
        ]
        image_items = [
            (
                _b64decode(img.data_base64),
                {
                    **metadata,
                    "engine": result.engine,
                    "page_number": img.page_number,
                    "total_pages": result.page_count,
                    "label": img.label,
                    "confidence": img.confidence,
                    "media_type": img.media_type,
                },
            )
            for img in result.images
        ]
        if not text_documents and not image_items:
            return None
        return text_documents, image_items

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
                f"No local loader for {ext!r} and no extraction service is "
                "configured. Supported without one: "
                f"{sorted(_LOCAL_FALLBACK_EXTENSIONS)}."
            ) from exc
        return await loader.load(source, metadata=metadata)


def _b64decode(data: str) -> bytes:
    import base64

    return base64.b64decode(data)


class RagLoadError(RuntimeError):
    """Raised when no loader (local or extraction-service) can handle a source."""


__all__ = ["LocalRagBackend", "RagLoadError"]
