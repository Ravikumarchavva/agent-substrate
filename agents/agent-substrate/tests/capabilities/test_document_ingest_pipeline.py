"""DocumentIngestPipeline — exercised against fake extraction/embed/store/
blob clients, never real services (those are covered by document_intelligence's
and embedding_reranker's own test suites)."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.capabilities.knowledge.document_ingest_pipeline import (
    DocumentIngestPipeline,
    ExtractionFailedError,
)
from substrate.runtimes.document_intelligence.client import ExtractResponse


class _FakeExtractionClient:
    """``extract_batch`` calls are recorded so tests can assert the pipeline
    actually made ONE batched call, not N sequential ones."""

    def __init__(self, responses: dict[str, ExtractResponse]) -> None:
        self._responses = responses
        self.batch_calls: list[list[str]] = []

    async def extract(
        self, data: bytes, filename: str, content_type: str
    ) -> ExtractResponse:
        return self._responses[filename]

    async def extract_batch(
        self, items: list[tuple[bytes, str, str]]
    ) -> list[ExtractResponse]:
        filenames = [filename for _, filename, _ in items]
        self.batch_calls.append(filenames)
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
    def __init__(self) -> None:
        self.uploaded: dict[str, bytes] = {}

    async def upload(self, key: str, data: bytes, *, content_type: str) -> None:
        self.uploaded[key] = data


def _pipeline(responses: dict[str, ExtractResponse]):
    extraction = _FakeExtractionClient(responses)
    embedder = _FakeEmbedder()
    store = _FakeStore()
    blob_store = _FakeBlobStore()
    pipeline = DocumentIngestPipeline(extraction, embedder, store, blob_store)
    return pipeline, extraction, store, blob_store


def _write_pdf(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 fake\n%%EOF")
    return p


async def test_ingest_files_makes_one_batched_extraction_call(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    b = _write_pdf(tmp_path, "b.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        ),
        "b.pdf": ExtractResponse(
            success=True, markdown="# B\n\nBody text for document B.", page_count=1
        ),
    }
    pipeline, extraction, store, blob_store = _pipeline(responses)

    results = await pipeline.ingest_files([a, b], collection="kb")

    assert len(extraction.batch_calls) == 1
    assert extraction.batch_calls[0] == ["a.pdf", "b.pdf"]
    assert results == [(1, 0), (1, 0)]
    assert blob_store.uploaded["kb/a.pdf"] == a.read_bytes()
    assert blob_store.uploaded["kb/b.pdf"] == b.read_bytes()


async def test_ingest_files_preserves_order_with_one_extraction_failure(tmp_path):
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
    pipeline, extraction, store, blob_store = _pipeline(responses)

    results = await pipeline.ingest_files([a, b, c], collection="kb")

    assert len(extraction.batch_calls) == 1  # still one call for all 3
    assert results[0] == (1, 0)
    assert isinstance(results[1], ExtractionFailedError)
    assert "security scan blocked" in str(results[1])
    assert results[2] == (1, 0)


async def test_ingest_files_unreadable_file_does_not_break_the_batch(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    missing = tmp_path / "does-not-exist.pdf"
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        )
    }
    pipeline, extraction, store, blob_store = _pipeline(responses)

    results = await pipeline.ingest_files([a, missing], collection="kb")

    # The unreadable file never made it into the batch extraction call.
    assert extraction.batch_calls == [["a.pdf"]]
    assert results[0] == (1, 0)
    assert isinstance(results[1], OSError)


async def test_ingest_files_empty_list_returns_empty_without_a_request():
    responses: dict[str, ExtractResponse] = {}
    pipeline, extraction, store, blob_store = _pipeline(responses)

    results = await pipeline.ingest_files([], collection="kb")

    assert results == []
    assert extraction.batch_calls == []


async def test_ingest_file_single_still_works(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {
        "a.pdf": ExtractResponse(
            success=True, markdown="# A\n\nBody text for document A.", page_count=1
        )
    }
    pipeline, extraction, store, blob_store = _pipeline(responses)

    n_text, n_image = await pipeline.ingest_file(a, collection="kb")

    assert (n_text, n_image) == (1, 0)
    assert store.added[0][1] == "kb"
    assert blob_store.uploaded["kb/a.pdf"] == a.read_bytes()


async def test_ingest_file_raises_extraction_failed_error_on_failure(tmp_path):
    a = _write_pdf(tmp_path, "a.pdf")
    responses = {"a.pdf": ExtractResponse(success=False, error="blocked")}
    pipeline, extraction, store, blob_store = _pipeline(responses)

    with pytest.raises(ExtractionFailedError, match="blocked"):
        await pipeline.ingest_file(a, collection="kb")
