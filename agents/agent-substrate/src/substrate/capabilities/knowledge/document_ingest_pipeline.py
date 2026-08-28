"""Multimodal PDF dataset ingestion — extract → chunk → embed → store.

Composes existing agent-substrate pieces without reimplementing any of them:
  - document-intelligence (PaddleOCR layout extraction) via ``ExtractionClient``
    (HTTP) — see runtimes/document_intelligence/client.py
  - ``StructureAwareChunker`` — markdown-heading-aware text chunking, see
    capabilities/knowledge/chunking.py
  - llama-embed/llama-rerank sidecars via ``EmbeddingReranker`` (HTTP) — text
    and image embedding into the same vector space, see
    runtimes/embedding_reranker/service/embedding.py
  - an optional ``blob_store`` (duck-typed ``upload``/``download``, e.g.
    ``S3FileStore`` — SeaweedFS/AWS S3, or any S3-compatible object store a deployment
    points it at, including a hosted one) for the source PDF and each
    extracted image, following the same pattern as
    ``backends/local.py``'s ``_store_image_bytes``/``_rehydrate_image``.

Distinct from ``RAGPipeline`` (pipeline.py): that one is text-only, driven
by the generic ``EmbeddingClient`` Protocol. This starts from raw PDF files
and produces multimodal (text + real image) ``Document`` objects via the
multimodal-specific ``EmbeddingReranker``.

Object storage is optional, not assumed: pass ``blob_store=None`` (the
default) to inline extracted images as ``ImageBlock(data=...)``, exactly as
a store-free deployment always has. Pass a blob store to instead write each
image's bytes there and keep only a small ``image_key`` reference in the
row's metadata — real, measured motivation: inlining serializes straight to
base64 in ``PgVectorStore``'s ``content_json`` JSONB column, and at
benchmark scale that's tens of thousands of multi-KB blobs bloating every
row. An upload failure degrades to inlining that one image rather than
losing it, matching ``backends/local.py``'s existing behavior.

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
        blob_store=blob_store,  # or None to inline images
    )
    stats = await pipeline.ingest_dataset(Path("data/pdfs"), collection="kb", limit=5)
    await pipeline.aclose()
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from substrate.kernel.storage.vector import VectorStore
    from substrate.runtimes.document_intelligence.client import (
        ExtractionClient,
        ExtractResponse,
    )
    from substrate.runtimes.embedding_reranker.service.embedding import (
        EmbeddingReranker,
    )

logger = logging.getLogger(__name__)


class ExtractionFailedError(RuntimeError):
    """Raised when document-intelligence reports ``success=False`` — e.g. the
    file was blocked by its security scan, or PaddleOCR itself couldn't
    parse it. Distinct from a transport failure (``ExtractionClient.extract``
    never raises); this is a real per-file outcome the caller should count
    as failed, not as "succeeded with 0 chunks"."""


@dataclass(slots=True)
class ExtractedFile:
    """One file's extraction result, ready for the chunk/embed/store tail —
    the hand-off unit between ``extract_files`` and ``process_extracted``.

    Deliberately does NOT carry the source PDF's raw bytes: a 450-page
    report can be tens of MB, and holding that (times however many files are
    in flight) is the single largest contributor to memory if this is queued
    between two independently-paced stages. ``process_extracted`` re-reads
    the file from disk instead — one extra threaded read is far cheaper than
    holding the bytes for the file's entire time in a hand-off queue.
    """

    path: Path
    result: ExtractResponse
    pages: int


class DocumentIngestPipeline:
    """Ingest raw PDFs into a ``VectorStore`` as multimodal ``Document``s.

    Args:
        extraction_client: Pre-built ``ExtractionClient`` pointed at a
            document-intelligence deployment.
        embedder: Pre-built ``EmbeddingReranker`` pointed at the
            llama-embed/llama-rerank sidecars.
        store: Any ``VectorStore`` implementation (``InMemoryVectorStore``/
            ``LanceDBVectorStore`` for dev, ``PgVectorStore`` for production).
        blob_store: Optional duck-typed object store (``async upload(key,
            data, *, content_type)`` / ``async download(key)`` — e.g.
            ``S3FileStore``). ``None`` (the default) inlines images instead;
            see the module docstring.
        key_prefix: Prepended to every blob key this pipeline writes
            (``f"{key_prefix}{collection}/..."``). Key *layout* is
            deployment policy, not something this pipeline should hardcode —
            e.g. a serving layer that authorizes objects by key prefix needs
            this to be predictable and distinct from other namespaces
            (unrelated to storage *location*, which is entirely a property
            of the injected ``store``/``blob_store`` instances). Defaults to
            ``""`` (unchanged, top-level keys).
        upload_concurrency: Max concurrent blob uploads, held on the
            instance so the cap is global across every file this pipeline
            processes, not per-file — see ``_embed_images``.
        chunk_size: Characters per text chunk (``StructureAwareChunker``).
        chunk_overlap: Overlap characters between consecutive chunks.
    """

    def __init__(
        self,
        extraction_client: ExtractionClient,
        embedder: EmbeddingReranker,
        store: VectorStore,
        *,
        blob_store: Any | None = None,
        key_prefix: str = "",
        upload_concurrency: int = 32,
        chunk_size: int = 1800,
        chunk_overlap: int = 250,
    ) -> None:
        from substrate.capabilities.knowledge.chunking import StructureAwareChunker

        self._extraction = extraction_client
        self._embedder = embedder
        self._chunker = StructureAwareChunker(
            chunk_size=chunk_size, overlap=chunk_overlap
        )
        self._store = store
        self._blob_store = blob_store
        self._key_prefix = key_prefix
        self._upload_sem = asyncio.Semaphore(upload_concurrency)

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
            vecs = await self._embedder.embed_texts([c.content[0].text for c in chunks])
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

    async def _upload_blob(self, key: str, data: bytes, media_type: str) -> bool:
        """Upload one blob (image or source PDF) under the instance-wide
        upload semaphore. Returns whether it succeeded — the caller degrades
        to inlining on failure rather than losing the image (matches
        ``backends/local.py::_store_image_bytes``'s degrade-not-drop
        behavior)."""
        async with self._upload_sem:
            try:
                await self._blob_store.upload(key, data, content_type=media_type)
                return True
            except Exception as exc:
                logger.warning("Storing image %s failed: %s", key, exc)
                return False

    async def _embed_images(
        self,
        images: list,
        source_name: str,
        *,
        total_pages: int,
        collection: str,
        pdf_key: str | None,
    ) -> list:
        """Embed a file's images in ONE batched request — same win and same
        batch-then-fallback shape as ``_embed_chunks`` (real, verified: 3
        images in one request vs 3 sequential round trips; see
        EmbeddingReranker.embed_images's own docstring). Falls back to
        per-image (skipping just the offending one) only if the batch
        itself fails.

        When ``self._blob_store`` is set, each image is uploaded there
        (concurrently, bounded by ``self._upload_sem``) and the row carries
        a small ``TextBlock`` caption + an ``image_key`` metadata reference
        — never a raw-bytes ``ImageBlock``, which the caption approach also
        makes lexically searchable via hybrid/lexical search for free. On
        upload failure, or when no blob store is configured, the row falls
        back to inlining the bytes as ``ImageBlock(data=...)`` — degrade,
        never drop.
        """
        from substrate.kernel.core.content import ImageBlock, TextBlock
        from substrate.kernel.storage.vector import Document
        from substrate.runtimes.embedding_reranker.service.embedding import (
            EmbeddingServiceError,
        )

        if not images:
            return []

        async def _doc(img, embedding: list[float], img_bytes: bytes) -> Document:
            base_meta = {
                "source": source_name,
                "page_number": img.page_number,
                "image_id": img.id,
                "confidence": img.confidence,
                "total_pages": total_pages,
                "kind": "image",
            }
            if pdf_key is not None:
                base_meta["pdf_key"] = pdf_key

            if self._blob_store is not None:
                ext = self._EXT_BY_MEDIA_TYPE.get(img.media_type, ".bin")
                key = (
                    f"{self._key_prefix}{collection}/{source_name}/images/{img.id}{ext}"
                )
                if await self._upload_blob(key, img_bytes, img.media_type):
                    caption = img.caption or f"[{img.label or 'image'}]"
                    return Document(
                        content=[TextBlock(text=caption)],
                        embedding=embedding,
                        metadata={
                            **base_meta,
                            "image_key": key,
                            "media_type": img.media_type,
                        },
                    )
            # No blob store, or the upload failed: keep the old inline
            # behavior rather than dropping the image entirely.
            return Document(
                content=[ImageBlock(data=img_bytes, media_type=img.media_type)],
                embedding=embedding,
                metadata=base_meta,
            )

        raw = await asyncio.to_thread(_decode_images, images)
        try:
            vecs = await self._embedder.embed_images(raw)
            return list(
                await asyncio.gather(
                    *(_doc(img, v, b) for img, v, b in zip(images, vecs, raw))
                )
            )
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

    # ── Stage 1: extraction ──────────────────────────────────────────────

    async def extract_files(
        self, paths: list[Path], *, timeout_s: float | None = None
    ) -> list[ExtractedFile | Exception]:
        """Extract every file in ONE document-intelligence batch call.

        Real motivation: a single document's pages often don't carry enough
        text regions to fill a large OCR batch on their own (see
        ``ExtractionPipeline.extract_batch``'s docstring) — grouping several
        files' pages into one ``predict()`` call gives document-intelligence
        real cross-document batching headroom that one-request-per-file
        leaves unused.

        Returns one entry per path, in ``paths`` order: an ``ExtractedFile``
        on success, or the ``OSError``/``ExtractionFailedError`` that failed
        that specific file — collected rather than raised, since one bad
        file in a batch of N must not lose the other N-1 files' results.
        This is the stage boundary for a staged producer/consumer driver
        (e.g. ``pdfqa_rag.pipeline.dataset_ingest_gpu``); ``ingest_file``/
        ``ingest_dataset`` below run both stages back-to-back for simpler
        callers that don't need the split.

        ``timeout_s`` overrides the extraction client's own default for
        this call only — a caller that knows the batch's total page count
        can size the timeout to it (a fixed timeout shared across
        wildly-different-sized batches was a real, measured cause of
        avoidable failures on large documents).
        """
        if not paths:
            return []

        read: list[bytes | OSError] = await asyncio.gather(
            *(_read_bytes(p) for p in paths)
        )

        readable = [(p, d) for p, d in zip(paths, read) if isinstance(d, bytes)]
        if readable:
            extraction_results = await self._extraction.extract_batch(
                [(data, p.name, "application/pdf") for p, data in readable],
                timeout_s=timeout_s,
            )
            result_by_path = {p: r for (p, _), r in zip(readable, extraction_results)}
        else:
            result_by_path = {}

        out: list[ExtractedFile | Exception] = []
        for p, data in zip(paths, read):
            if isinstance(data, OSError):
                out.append(data)
                continue
            result = result_by_path[p]
            if not result.success:
                out.append(
                    ExtractionFailedError(result.error or "unknown extraction failure")
                )
                continue
            out.append(ExtractedFile(path=p, result=result, pages=result.page_count))
        return out

    # ── Stage 2: chunk, embed, store ─────────────────────────────────────

    async def process_extracted(
        self, item: ExtractedFile, *, collection: str
    ) -> tuple[int, int]:
        """Chunk, embed, and store one already-extracted document.

        Returns ``(n_text_docs, n_image_docs)`` — may be fewer than the
        chunker/extractor produced if individual chunks/images fail to embed
        (e.g. an HTML-table chunk too large for the sidecar's context; see
        ``_embed_chunks``), which are skipped and logged, not fatal.
        """
        result = item.result
        path = item.path

        pdf_key: str | None = None
        if self._blob_store is not None:
            data = await asyncio.to_thread(path.read_bytes)
            pdf_key = f"{self._key_prefix}{collection}/{path.name}"
            if not await self._upload_blob(pdf_key, data, "application/pdf"):
                pdf_key = None

        # StructureAwareChunker never splits mid-sentence, but the extracted
        # markdown embeds tables as raw HTML (document-intelligence's own
        # convention) — an HTML table has ~no ". "/"! "/"? " boundaries, so it
        # can collapse into one oversized "sentence" that exceeds the embed
        # sidecar's token ceiling (--ctx-size/--parallel -> tokens/slot).
        # Real, found-not-assumed: a 4158-char/1983-token chunk from an HTML
        # table hit exactly this — see _embed_chunks for how it's handled.
        chunk_meta = {
            "source": path.name,
            "total_pages": result.page_count,
            "kind": "text",
        }
        if pdf_key is not None:
            chunk_meta["pdf_key"] = pdf_key
        chunks = await asyncio.to_thread(
            self._chunker.chunk, result.markdown, chunk_meta
        )

        text_docs = await self._embed_chunks(chunks, path.name)
        image_docs = await self._embed_images(
            result.images,
            path.name,
            total_pages=result.page_count,
            collection=collection,
            pdf_key=pdf_key,
        )

        # One write, not two: makes per-file persistence atomic (a crash
        # between two separate add() calls previously could strand text
        # rows with no checkpoint entry, which a resume would then
        # re-insert under fresh UUIDs).
        all_docs = text_docs + image_docs
        if all_docs:
            await self._store.add(all_docs, collection=collection)
        return len(text_docs), len(image_docs)

    # ── Single file (both stages) ────────────────────────────────────────

    async def ingest_file(self, path: Path, *, collection: str) -> tuple[int, int]:
        """Extract, chunk, embed, and store one PDF — ``extract_files`` +
        ``process_extracted`` run back-to-back.

        Raises ``OSError`` if the file can't be read, or
        ``ExtractionFailedError`` if document-intelligence reports failure
        (e.g. blocked by its security scan) — callers doing dataset-level
        ingestion should catch both per-file so one bad PDF doesn't kill the
        whole batch.
        """
        extracted = await self.extract_files([path])
        item = extracted[0]
        if isinstance(item, Exception):
            raise item
        return await self.process_extracted(item, collection=collection)

    # ── Dataset directory (both stages, many files) ──────────────────────

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
        ``/v1/extract`` calls (e.g. multiple replicas behind a load balancer
        — see ``pdfqa_rag.pipeline.dataset_ingest_gpu`` for a driver built
        around exactly that).

        One failing file is logged and counted, not raised — a 5000-file
        batch shouldn't die on file #3.
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
                logger.info(
                    "Ingested %s: %d text, %d image docs", p.name, n_text, n_img
                )

        await asyncio.gather(*(_one(p) for p in pdfs))
        return stats


def _decode_images(images: list) -> list[bytes]:
    """Sync, CPU-bound — callers run this via ``asyncio.to_thread``. A
    240-image response is tens of MB of base64 decode, otherwise done
    directly on the event loop shared by every other concurrent worker."""
    return [base64.b64decode(img.data_base64) for img in images]


async def _read_bytes(path: Path) -> bytes | OSError:
    try:
        return await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        return exc


__all__ = ["DocumentIngestPipeline", "ExtractedFile", "ExtractionFailedError"]
