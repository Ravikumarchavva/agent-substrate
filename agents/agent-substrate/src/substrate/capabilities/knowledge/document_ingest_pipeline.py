"""Multimodal PDF dataset ingestion — extract → chunk → embed → store.

Composes existing agent-substrate pieces without reimplementing any of them:
  - document-intelligence (PaddleOCR layout extraction) via ``ExtractionClient``
    (HTTP) — see runtimes/document_intelligence/client.py
  - ``StructureAwareChunker`` — markdown-heading-aware text chunking, see
    capabilities/knowledge/chunking.py
  - llama-embed/llama-rerank sidecars via ``EmbeddingReranker`` (HTTP) — text
    and image embedding into the same vector space, see
    runtimes/embedding_reranker/service/embedding.py
  - ``S3FileStore`` (SeaweedFS/MinIO/S3) — durable object storage for the
    original PDF and each extracted image, see capabilities/storage/s3.py

Distinct from ``RAGPipeline`` (pipeline.py): that one is text-only, driven
by the generic ``EmbeddingClient`` Protocol. This starts from raw PDF files
and produces multimodal (text + real image) ``Document`` objects via the
multimodal-specific ``EmbeddingReranker``.

Images and the source PDF are stored in ``blob_store``, not inlined —
``ImageBlock.data`` (raw bytes) would otherwise serialize to base64 straight
into ``PgVectorStore``'s ``content_json`` JSONB column (real, measured: at
benchmark scale that's tens of thousands of multi-KB base64 blobs bloating
every row). Documents instead carry ``ImageBlock.storage_key`` (a durable
ref, resolved to a real URL only at display time via
``S3FileStore.presign_url``) plus a ``pdf_storage_key`` in metadata so a
UI can link a citation back to its exact source PDF.

Usage::

    from substrate.runtimes.document_intelligence.client import ExtractionClient
    from substrate.runtimes.embedding_reranker.service.embedding import EmbeddingReranker
    from substrate.capabilities.storage.s3 import S3FileStore
    from substrate.capabilities.knowledge.document_ingest_pipeline import DocumentIngestPipeline

    pipeline = DocumentIngestPipeline(
        ExtractionClient(base_url="http://localhost:8021"),
        EmbeddingReranker(embed_server_url="http://localhost:8031",
                          rerank_server_url="http://localhost:8032"),
        store,
        blob_store,
    )
    stats = await pipeline.ingest_dataset(Path("data/pdfs"), collection="kb", limit=5)
    await pipeline.aclose()
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from substrate.capabilities.storage.s3 import S3FileStore
    from substrate.kernel.storage.vector import VectorStore
    from substrate.runtimes.document_intelligence.client import ExtractionClient
    from substrate.runtimes.embedding_reranker.service.embedding import EmbeddingReranker

logger = logging.getLogger(__name__)


class ExtractionFailedError(RuntimeError):
    """Raised when document-intelligence reports ``success=False`` — e.g. the
    file was blocked by its security scan, or PaddleOCR itself couldn't
    parse it. Distinct from a transport failure (``ExtractionClient.extract``
    never raises); this is a real per-file outcome the caller should count
    as failed, not as "succeeded with 0 chunks"."""


class DocumentIngestPipeline:
    """Ingest raw PDFs into a ``VectorStore`` as multimodal ``Document``s.

    Args:
        extraction_client: Pre-built ``ExtractionClient`` pointed at a
            document-intelligence deployment.
        embedder: Pre-built ``EmbeddingReranker`` pointed at the
            llama-embed/llama-rerank sidecars.
        store: Any ``VectorStore`` implementation (``InMemoryVectorStore``/
            ``LanceDBVectorStore`` for dev, ``PgVectorStore`` for production).
        blob_store: Pre-built ``S3FileStore`` (SeaweedFS/MinIO/S3) that the
            source PDF and every extracted image are uploaded to — their
            ``storage_key`` is what gets persisted, not raw bytes.
        chunk_size: Characters per text chunk (``StructureAwareChunker``).
        chunk_overlap: Overlap characters between consecutive chunks.
    """

    def __init__(
        self,
        extraction_client: ExtractionClient,
        embedder: EmbeddingReranker,
        store: VectorStore,
        blob_store: S3FileStore,
        *,
        chunk_size: int = 1800,
        chunk_overlap: int = 250,
    ) -> None:
        from substrate.capabilities.knowledge.chunking import StructureAwareChunker

        self._extraction = extraction_client
        self._embedder = embedder
        self._chunker = StructureAwareChunker(chunk_size=chunk_size, overlap=chunk_overlap)
        self._store = store
        self._blob_store = blob_store

    async def aclose(self) -> None:
        await self._embedder.aclose()

    async def _embed_chunks(self, chunks: list, source_name: str) -> list:
        """Embed a file's chunks in ONE batched request (real, verified: 3
        texts in 0.04s batched vs full network RTT x3 sequential — see
        EmbeddingReranker.embed_texts's own docstring). Falls back to the
        original one-at-a-time loop (skipping just the offending chunk) only
        if the batch itself fails — a single oversized chunk 400s the WHOLE
        batch with no partial results (also verified against the real
        sidecar), so the fast path can't tell us which chunk was bad.
        """
        from substrate.runtimes.embedding_reranker.service.embedding import (
            EmbeddingServiceError,
        )

        if not chunks:
            return []

        try:
            vecs = await self._embedder.embed_texts(
                [c.content[0].text for c in chunks]
            )
            return [replace(c, embedding=v) for c, v in zip(chunks, vecs)]
        except EmbeddingServiceError as exc:
            logger.warning(
                "Batch embed failed for %s (%s) — falling back to per-chunk",
                source_name,
                exc,
            )

        text_docs = []
        for c in chunks:
            try:
                vec = await self._embedder.embed_text(c.content[0].text)
            except EmbeddingServiceError as exc:
                logger.warning(
                    "Skipping chunk %s in %s: %s",
                    c.metadata.get("chunk_index"),
                    source_name,
                    exc,
                )
                continue
            text_docs.append(replace(c, embedding=vec))
        return text_docs

    _EXT_BY_MEDIA_TYPE = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }

    async def _embed_images(
        self,
        images: list,
        source_name: str,
        *,
        total_pages: int,
        collection: str,
        pdf_storage_key: str,
    ) -> list:
        """Embed a file's images in ONE batched request — same win and same
        batch-then-fallback shape as ``_embed_chunks`` (real, verified: 3
        images in one request vs 3 sequential round trips; see
        EmbeddingReranker.embed_images's own docstring). Falls back to
        per-image (skipping just the offending one) only if the batch
        itself fails.

        Each image is also uploaded to ``blob_store`` and referenced by
        ``storage_key`` — the Document itself never carries the raw bytes
        (see this module's docstring for why).
        """
        from substrate.kernel.core.content import ImageBlock
        from substrate.kernel.storage.vector import Document
        from substrate.runtimes.embedding_reranker.service.embedding import (
            EmbeddingServiceError,
        )

        if not images:
            return []

        async def _doc(img, embedding: list[float], img_bytes: bytes) -> Document:
            ext = self._EXT_BY_MEDIA_TYPE.get(img.media_type, ".bin")
            key = f"{collection}/{source_name}/images/{img.id}{ext}"
            await self._blob_store.upload(key, img_bytes, content_type=img.media_type)
            return Document(
                content=[ImageBlock(storage_key=key, media_type=img.media_type)],
                embedding=embedding,
                metadata={
                    "source": source_name,
                    "page_number": img.page_number,
                    "image_id": img.id,
                    "confidence": img.confidence,
                    "total_pages": total_pages,
                    "pdf_storage_key": pdf_storage_key,
                },
            )

        raw = [base64.b64decode(img.data_base64) for img in images]
        try:
            vecs = await self._embedder.embed_images(raw)
            return [await _doc(img, v, b) for img, v, b in zip(images, vecs, raw)]
        except EmbeddingServiceError as exc:
            logger.warning(
                "Batch image embed failed for %s (%s) — falling back to per-image",
                source_name,
                exc,
            )

        image_docs = []
        for img, img_bytes in zip(images, raw):
            try:
                vec = await self._embedder.embed_image(img_bytes)
            except EmbeddingServiceError as exc:
                logger.warning("Skipping image %s in %s: %s", img.id, source_name, exc)
                continue
            image_docs.append(await _doc(img, vec, img_bytes))
        return image_docs

    # ── Single file ───────────────────────────────────────────────────────

    async def ingest_file(self, path: Path, *, collection: str) -> tuple[int, int]:
        """Extract, chunk, embed, and store one PDF.

        Returns ``(n_text_docs, n_image_docs)`` — may be fewer than the
        chunker/extractor produced if individual chunks/images fail to embed
        (e.g. an HTML-table chunk too large for the sidecar's context; see
        the loop below), which are skipped and logged, not fatal. Raises
        ``OSError`` if the file can't be read, or ``ExtractionFailedError``
        if document-intelligence reports failure (e.g. blocked by its
        security scan) — callers doing dataset-level ingestion should catch
        both per-file so one bad PDF doesn't kill the whole batch.
        """
        data = path.read_bytes()
        result = await self._extraction.extract(data, path.name, "application/pdf")
        if not result.success:
            raise ExtractionFailedError(result.error or "unknown extraction failure")

        # Durable ref to the exact source PDF, so a citation can link back to
        # "view page N of this file" — the local dataset path isn't a durable
        # store, it's gone the moment this eval run's machine is torn down.
        pdf_key = f"{collection}/{path.name}"
        await self._blob_store.upload(pdf_key, data, content_type="application/pdf")

        # StructureAwareChunker never splits mid-sentence, but the extracted
        # markdown embeds tables as raw HTML (document-intelligence's own
        # convention) — an HTML table has ~no ". "/"! "/"? " boundaries, so it
        # can collapse into one oversized "sentence" that exceeds the embed
        # sidecar's token ceiling (--ctx-size/--parallel -> 1024 tokens/slot).
        # Real, found-not-assumed: a 4158-char/1983-token chunk from an HTML
        # table hit exactly this — see _embed_chunks for how it's handled.
        chunks = self._chunker.chunk(
            result.markdown,
            metadata={
                "source": path.name,
                "total_pages": result.page_count,
                "pdf_storage_key": pdf_key,
            },
        )
        text_docs = await self._embed_chunks(chunks, path.name)
        image_docs = await self._embed_images(
            result.images,
            path.name,
            total_pages=result.page_count,
            collection=collection,
            pdf_storage_key=pdf_key,
        )

        if text_docs:
            await self._store.add(text_docs, collection=collection)
        if image_docs:
            await self._store.add(image_docs, collection=collection)
        return len(text_docs), len(image_docs)

    # ── Dataset directory ────────────────────────────────────────────────

    async def ingest_dataset(
        self,
        dataset_dir: Path,
        *,
        collection: str,
        limit: int | None = None,
        concurrency: int = 1,
    ) -> dict[str, int]:
        """Ingest up to ``limit`` PDFs found anywhere under ``dataset_dir``.

        ``concurrency`` defaults to 1: real, found-not-assumed — running 2-3
        files concurrently against document-intelligence-gpu made even small
        (3-5 page) files time out, while the same files succeeded instantly
        run one at a time. It appears to serialize internally (single
        PPStructureV3 pipeline instance) rather than being safe for
        concurrent requests. Only raise this if you've verified your
        document-intelligence deployment actually handles concurrent
        ``/v1/extract`` calls (e.g. multiple replicas behind a load balancer).

        Runs ``concurrency`` files in parallel (each file's own chunks are
        still embedded sequentially — the sidecars only take one connection
        of real work at a time per request anyway). One failing file is
        logged and counted, not raised — a 5000-file batch shouldn't die on
        file #3.
        """
        from substrate.runtimes.embedding_reranker.service.embedding import (
            EmbeddingServiceError,
        )

        pdfs = sorted(dataset_dir.glob("**/*.pdf"))
        if limit is not None:
            pdfs = pdfs[:limit]

        sem = asyncio.Semaphore(concurrency)
        stats = {"files": 0, "failed": 0, "text_docs": 0, "image_docs": 0}

        async def _one(p: Path) -> None:
            async with sem:
                try:
                    n_text, n_img = await self.ingest_file(p, collection=collection)
                except (OSError, EmbeddingServiceError, ExtractionFailedError) as exc:
                    stats["failed"] += 1
                    logger.warning("Failed to ingest %s: %s", p.name, exc)
                    return
                stats["files"] += 1
                stats["text_docs"] += n_text
                stats["image_docs"] += n_img
                logger.info("Ingested %s: %d text, %d image docs", p.name, n_text, n_img)

        await asyncio.gather(*(_one(p) for p in pdfs))
        return stats


__all__ = ["DocumentIngestPipeline", "ExtractionFailedError"]
