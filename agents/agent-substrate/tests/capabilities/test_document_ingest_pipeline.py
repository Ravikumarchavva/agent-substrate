"""DocumentIngestPipeline — exercised against fake extraction/embed/store/
blob clients, never real services (those are covered by document_intelligence's
and embedding_reranker's own test suites)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from substrate.capabilities.knowledge.document_ingest_pipeline import (
    DocumentIngestPipeline,
    ExtractedFile,
    ExtractionFailedError,
)
from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.runtimes.document_intelligence.client import (
    ExtractedImage,
    ExtractResponse,
)


def _image(id: str = "img-p1-0", caption: str | None = "a chart") -> ExtractedImage:
    return ExtractedImage(
        data_base64=base64.b64encode(b"fake-png-bytes").decode("ascii"),
        media_type="image/png",
        page_number=1,
        label="chart",
        confidence=0.95,
        caption=caption,
        id=id,
    )


class _FakeExtractionClient:
    """``extract_batch`` calls are recorded so tests can assert the pipeline
    actually made ONE batched call, not N sequential ones."""

    def __init__(self, responses: dict[str, ExtractResponse]) -> None:
        self._responses = responses
        self.batch_calls: list[list[str]] = []
        self.batch_timeouts: list[float | None] = []

    async def extract(
        self, data: bytes, filename: str, content_type: str, *, timeout_s=None
    ) -> ExtractResponse:
        return self._responses[filename]

    async def extract_batch(
        self, items: list[tuple[bytes, str, str]], *, timeout_s=None
    ) -> list[ExtractResponse]:
        filenames = [filename for _, filename, _ in items]
        self.batch_calls.append(filenames)
        self.batch_timeouts.append(timeout_s)
        return [self._responses[filename] for filename in filenames]


class _FakeEmbedder:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    async def embed_text(self, text: str) -> list[float]:
        return [0.1, 0.2]

    async def embed_images(self, images: list[bytes]) -> list[list[float]]:
        return [[0.3, 0.4] for _ in images]

    async def embed_image(self, data: bytes) -> list[float]:
        return [0.3, 0.4]

    async def aclose(self) -> None:
        pass


class _FakeStore:
    def __init__(self) -> None:
        self.added: list[tuple[list, str]] = []

    async def add(self, documents: list, *, collection: str) -> list[str]:
        self.added.append((documents, collection))
        return [d.id or "generated-id" for d in documents]


class _FakeBlobStore:
    def __init__(self, *, fail_keys: set[str] | None = None) -> None:
        self.uploaded: dict[str, bytes] = {}
        self._fail_keys = fail_keys or set()

    async def upload(self, key: str, data: bytes, *, content_type: str) -> None:
        if key in self._fail_keys:
            raise RuntimeError(f"simulated upload failure for {key}")
        self.uploaded[key] = data


def _pipeline(responses: dict[str, ExtractResponse], *, blob_store=None, **kwargs):
    extraction = _FakeExtractionClient(responses)
    embedder = _FakeEmbedder()
    store = _FakeStore()
    pipeline = DocumentIngestPipeline(
        extraction, embedder, store, blob_store=blob_store, **kwargs
    )
    return pipeline, extraction, store


def _write_pdf(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 fake\n%%EOF")
    return p


# ── The crash-bug regression: images must never break construction ──────────


async def test_ingest_file_with_images_and_no_blob_store_inlines_them(tmp_path):
    """This is the bug that started this work: an ExtractResponse carrying
    images used to construct ImageBlock(storage_key=...) with no data/url/
    file_id, which ImageBlock's own validator rejects outright. Every real
    PDF with a chart/table would have crashed the worker. No fixture in this
    file's previous version exercised images at all, which is exactly why
    it went unnoticed."""
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True,
            markdown="# A\n\nBody text for document A.",
            page_count=1,
            images=[_image()],
        ),
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=None)

    n_text, n_image = await pipeline.ingest_file(a, collection="kb")

    assert n_image == 1
    docs, collection = store.added[0]
    assert collection == "kb"
    image_docs = [d for d in docs if isinstance(d.content[0], ImageBlock)]
    assert len(image_docs) == 1
    assert image_docs[0].content[0].data == b"fake-png-bytes"
    assert image_docs[0].metadata["kind"] == "image"


async def test_ingest_file_with_images_and_blob_store_stores_key_not_bytes(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True,
            markdown="# A\n\nBody text for document A.",
            page_count=1,
            images=[_image(id="img-p1-0")],
        ),
    }
    blob_store = _FakeBlobStore()
    pipeline, extraction, store = _pipeline(responses, blob_store=blob_store)

    await pipeline.ingest_file(a, collection="kb")

    docs, _ = store.added[0]
    image_docs = [d for d in docs if d.metadata.get("kind") == "image"]
    assert len(image_docs) == 1
    doc = image_docs[0]
    # Never a storage_key-only ImageBlock (the exact shape that used to crash).
    assert isinstance(doc.content[0], TextBlock)
    assert doc.content[0].text == "a chart"
    key = doc.metadata["image_key"]
    assert key == "kb/images/a.pdf/img-p1-0.png"
    assert blob_store.uploaded[key] == b"fake-png-bytes"


async def test_image_upload_failure_degrades_to_inline_not_dropped(tmp_path):
    """Storage is a nice-to-have; the image itself must survive an upload
    failure, matching backends/local.py's degrade-not-drop behavior."""
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True,
            markdown="# A\n\nBody text for document A.",
            page_count=1,
            images=[_image(id="img-p1-0")],
        ),
    }
    blob_store = _FakeBlobStore(fail_keys={"kb/images/a.pdf/img-p1-0.png"})
    pipeline, extraction, store = _pipeline(responses, blob_store=blob_store)

    n_text, n_image = await pipeline.ingest_file(a, collection="kb")

    assert n_image == 1
    docs, _ = store.added[0]
    image_docs = [d for d in docs if d.metadata.get("kind") == "image"]
    assert isinstance(image_docs[0].content[0], ImageBlock)
    assert image_docs[0].content[0].data == b"fake-png-bytes"


async def test_key_prefix_is_applied_to_both_pdf_and_image_keys(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True,
            markdown="# A\n\nBody text.",
            page_count=1,
            images=[_image(id="img-p1-0")],
        ),
    }
    blob_store = _FakeBlobStore()
    pipeline, extraction, store = _pipeline(
        responses, blob_store=blob_store, key_prefix="datasets/eval/"
    )

    await pipeline.ingest_file(a, collection="kb")

    assert "datasets/eval/kb/pdfs/a.pdf" in blob_store.uploaded
    assert "datasets/eval/kb/images/a.pdf/img-p1-0.png" in blob_store.uploaded


async def test_pdf_key_is_never_a_path_prefix_of_an_image_key(tmp_path):
    """Real bug, found via the SeaweedFS admin UI: image keys used to be
    "{collection}/{source_name}/images/{id}.png", making the PDF's own key
    ("{collection}/{source_name}") a literal path prefix of its images'
    keys. SeaweedFS's filer is filesystem-backed, so a key can't be both a
    leaf file and a directory at once -- the PDF object silently became
    inaccessible (showed as a "Directory" with no download action) the
    moment its first image was uploaded underneath it. No key here may
    ever be a path-prefix of another."""
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True,
            markdown="# A\n\nBody text.",
            page_count=1,
            images=[_image(id="img-p1-0"), _image(id="img-p1-1")],
        ),
    }
    blob_store = _FakeBlobStore()
    pipeline, extraction, store = _pipeline(responses, blob_store=blob_store)

    await pipeline.ingest_file(a, collection="kb")

    keys = list(blob_store.uploaded.keys())
    assert len(keys) == 3  # 1 pdf + 2 images
    for k1 in keys:
        for k2 in keys:
            if k1 != k2:
                assert not k2.startswith(k1 + "/"), f"{k1!r} is a prefix of {k2!r}"


async def test_one_store_add_call_per_file_not_two(tmp_path):
    """Text and image docs land in a single add() call so persistence is
    atomic -- a crash between two separate calls previously could strand
    text rows with no checkpoint entry."""
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True,
            markdown="# A\n\nBody text for document A.",
            page_count=1,
            images=[_image()],
        ),
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    await pipeline.ingest_file(a, collection="kb")

    assert len(store.added) == 1
    docs, _ = store.added[0]
    assert len(docs) == 2  # 1 text chunk + 1 image


# ── extract_files / process_extracted (the staged seam) ─────────────────────


async def test_extract_files_makes_one_batched_extraction_call(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    b = _write_pdf(tmp_path, "b.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        ),
        "b.pdf": ExtractResponse(
            success=True, markdown="# B\n\nBody text for document B.", page_count=3
        ),
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    results = await pipeline.extract_files([a, b])

    assert len(extraction.batch_calls) == 1
    assert extraction.batch_calls[0] == ["a.pdf", "b.pdf"]
    assert all(isinstance(r, ExtractedFile) for r in results)
    assert [r.path for r in results] == [a, b]
    assert [r.pages for r in results] == [1, 3]


async def test_extract_files_passes_timeout_through(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(success=True, markdown="# A\n\nBody.", page_count=1)
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    await pipeline.extract_files([a], timeout_s=123.0)

    assert extraction.batch_timeouts == [123.0]


async def test_extract_files_preserves_order_with_one_extraction_failure(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    b = _write_pdf(tmp_path, "b.pdf")
    c = _write_pdf(tmp_path, "c.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        ),
        "b.pdf": ExtractResponse(success=False, error="security scan blocked"),
        "c.pdf": ExtractResponse(
            success=True, markdown="# C\n\nBody text for document C.", page_count=1
        ),
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    results = await pipeline.extract_files([a, b, c])

    assert len(extraction.batch_calls) == 1  # still one call for all 3
    assert isinstance(results[0], ExtractedFile)
    assert isinstance(results[1], ExtractionFailedError)
    assert "security scan blocked" in str(results[1])
    assert isinstance(results[2], ExtractedFile)


async def test_extract_files_unreadable_file_does_not_break_the_batch(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    missing = tmp_path / "does-not-exist.pdf"
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        )
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    results = await pipeline.extract_files([a, missing])

    # The unreadable file never made it into the batch extraction call.
    assert extraction.batch_calls == [["a.pdf"]]
    assert isinstance(results[0], ExtractedFile)
    assert isinstance(results[1], OSError)


async def test_extract_files_empty_list_returns_empty_without_a_request():
    pipeline, extraction, store = _pipeline({}, blob_store=_FakeBlobStore())

    results = await pipeline.extract_files([])

    assert results == []
    assert extraction.batch_calls == []


async def test_process_extracted_then_store(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        )
    }
    blob_store = _FakeBlobStore()
    pipeline, extraction, store = _pipeline(responses, blob_store=blob_store)

    extracted = await pipeline.extract_files([a])
    n_text, n_image = await pipeline.process_extracted(extracted[0], collection="kb")

    assert (n_text, n_image) == (1, 0)
    assert store.added[0][1] == "kb"
    assert blob_store.uploaded["kb/pdfs/a.pdf"] == a.read_bytes()


# ── ingest_file / ingest_dataset (both stages, back-to-back) ────────────────


async def test_ingest_file_single_still_works(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        )
    }
    blob_store = _FakeBlobStore()
    pipeline, extraction, store = _pipeline(responses, blob_store=blob_store)

    n_text, n_image = await pipeline.ingest_file(a, collection="kb")

    assert (n_text, n_image) == (1, 0)
    assert store.added[0][1] == "kb"
    assert blob_store.uploaded["kb/pdfs/a.pdf"] == a.read_bytes()


async def test_ingest_file_without_blob_store_does_not_upload_the_pdf(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        )
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=None)

    n_text, n_image = await pipeline.ingest_file(a, collection="kb")

    assert (n_text, n_image) == (1, 0)
    docs, _ = store.added[0]
    assert "pdf_key" not in docs[0].metadata


async def test_ingest_file_raises_extraction_failed_error_on_failure(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {"a.pdf": ExtractResponse(success=False, error="blocked")}
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    with pytest.raises(ExtractionFailedError, match="blocked"):
        await pipeline.ingest_file(a, collection="kb")


async def test_ingest_dataset_counts_files_and_failures(tmp_path):
    _write_pdf(tmp_path, "a.pdf")
    _write_pdf(tmp_path, "b.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        ),
        "b.pdf": ExtractResponse(success=False, error="blocked"),
    }
    pipeline, extraction, store = _pipeline(responses, blob_store=_FakeBlobStore())

    stats = await pipeline.ingest_dataset(tmp_path, collection="kb")

    assert stats["files"] == 1
    assert stats["failed"] == 1
