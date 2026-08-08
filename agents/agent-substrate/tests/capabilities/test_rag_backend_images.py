"""LocalRagBackend — multimodal (chart/table image) ingest/query paths.

Covers what test_rag_backends.py doesn't: chart/table images extracted by
the extraction service bypass RAGPipeline entirely and go through a separate
embed/store/search path against a second (image) VectorStore — see
backends/local.py's module docstring."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock

from substrate.capabilities.knowledge.backends.local import LocalRagBackend
from substrate.capabilities.knowledge.extraction_client import (
    ExtractedImage,
    ExtractedPageText,
    ExtractResponse,
)
from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.kernel.storage.vector import Document, SearchResult


class StubImageStore:
    def __init__(self) -> None:
        self.documents: list[Document] = []
        self.added_collections: list[str] = []
        self.searched_with: list[list[float]] = []

    async def add(
        self, documents: list[Document], *, collection: str = "default"
    ) -> list[str]:
        self.documents.extend(documents)
        self.added_collections.append(collection)
        return [doc.id for doc in documents]

    async def search(
        self,
        query_embedding,
        *,
        collection: str = "default",
        limit: int = 5,
        filter=None,
    ) -> list[SearchResult]:
        self.searched_with.append(query_embedding)
        return [
            SearchResult(
                id=doc.id, content=doc.content, score=0.8, metadata=doc.metadata
            )
            for doc in self.documents[:limit]
        ]

    async def list_collections(self) -> list[str]:
        return ["default"]

    async def delete_collection(self, collection: str) -> int:
        return len(self.documents)


class StubPipeline:
    """Stands in for RAGPipeline — image ingest bypasses it entirely, so
    these tests only need ingest_documents/query to exist and be callable."""

    def __init__(self) -> None:
        self.ingested: list = []

    async def ingest_documents(self, documents, *, collection: str = "default") -> int:
        self.ingested.extend(documents)
        return len(documents)

    async def query(self, question, *, collection: str = "default", limit: int = 5):
        return []


def _backend(
    *, image_store=None, extraction_client=None, vector_store=None
) -> tuple[LocalRagBackend, StubPipeline]:
    pipeline = StubPipeline()
    backend = LocalRagBackend(
        pipeline,
        vector_store=vector_store,
        image_store=image_store,
        extraction_service_url="http://extraction-test:8080",
        extraction_client=extraction_client,
    )
    return backend, pipeline


# ── _ingest_images ──────────────────────────────────────────────────────────


async def test_ingest_images_skips_one_bad_image_without_failing_the_rest():
    image_store = StubImageStore()
    client = AsyncMock()
    client.embed_image = AsyncMock(side_effect=[None, [0.1, 0.2]])
    backend, _ = _backend(image_store=image_store, extraction_client=client)

    items = [
        (b"bad-image-bytes", {"label": "chart", "page_number": 1}),
        (b"good-image-bytes", {"label": "chart", "page_number": 2}),
    ]
    await backend._ingest_images(items, collection="kb")

    assert len(image_store.documents) == 1
    assert image_store.documents[0].metadata["page_number"] == 2
    assert image_store.documents[0].embedding == [0.1, 0.2]
    assert isinstance(image_store.documents[0].content[0], ImageBlock)
    assert image_store.added_collections == ["kb"]


async def test_ingest_images_noop_without_image_store():
    client = AsyncMock()
    backend, _ = _backend(image_store=None, extraction_client=client)

    await backend._ingest_images([(b"data", {})], collection="kb")

    client.embed_image.assert_not_called()


# ── _query_images ────────────────────────────────────────────────────────────


async def test_query_images_returns_empty_without_image_store():
    backend, _ = _backend(image_store=None, extraction_client=AsyncMock())
    assert await backend._query_images("q", collection="kb", limit=5) == []


async def test_query_images_returns_empty_when_embed_text_fails():
    image_store = StubImageStore()
    client = AsyncMock()
    client.embed_text = AsyncMock(return_value=None)
    backend, _ = _backend(image_store=image_store, extraction_client=client)

    results = await backend._query_images("show me the chart", collection="kb", limit=5)

    assert results == []


async def test_query_images_searches_image_store_with_embedded_query():
    image_store = StubImageStore()
    image_store.documents = [
        Document(
            content=[ImageBlock(data=b"png", media_type="image/png")], embedding=[0.5]
        )
    ]
    client = AsyncMock()
    client.embed_text = AsyncMock(return_value=[0.9, 0.1])
    backend, _ = _backend(image_store=image_store, extraction_client=client)

    results = await backend._query_images("show me the chart", collection="kb", limit=5)

    assert len(results) == 1
    assert image_store.searched_with == [[0.9, 0.1]]


# ── query() — text reranked, then images appended (not merged into rerank) ──


async def test_query_appends_image_results_after_text_reranking():
    image_store = StubImageStore()
    image_store.documents = [
        Document(
            content=[ImageBlock(data=b"png", media_type="image/png")], embedding=[0.5]
        )
    ]
    client = AsyncMock()
    client.embed_text = AsyncMock(return_value=[0.9])
    backend, pipeline = _backend(image_store=image_store, extraction_client=client)

    text_result = SearchResult(id="t1", content=[TextBlock(text="hello")], score=0.7)

    async def fake_query(question, *, collection="default", limit=5):
        return [text_result]

    pipeline.query = fake_query

    results = await backend.query("show me the chart", collection="kb", limit=5)

    assert results[0] is text_result
    assert len(results) == 2
    assert isinstance(results[1].content[0], ImageBlock)


# ── _load_via_extraction_service — splits text pages and images ────────────


async def test_load_via_extraction_service_splits_text_and_images():
    client = AsyncMock()
    img_bytes = b"fake-png-bytes"
    client.extract = AsyncMock(
        return_value=ExtractResponse(
            success=True,
            text="page1\n\npage2",
            pages=[
                ExtractedPageText(page_number=1, text="page1"),
                ExtractedPageText(page_number=2, text="page2"),
            ],
            images=[
                ExtractedImage(
                    data_base64=base64.b64encode(img_bytes).decode("ascii"),
                    page_number=1,
                    label="chart",
                    confidence=0.97,
                )
            ],
            engine="paddleocr",
            page_count=2,
        )
    )
    backend, _ = _backend(image_store=StubImageStore(), extraction_client=client)

    result = await backend._load_via_extraction_service(
        b"pdf bytes", "report.pdf", {"filename": "report.pdf"}
    )

    assert result is not None
    text_documents, image_items = result
    assert len(text_documents) == 2
    assert text_documents[0].metadata["page_number"] == 1
    assert text_documents[0].metadata["total_pages"] == 2

    assert len(image_items) == 1
    data, meta = image_items[0]
    assert data == img_bytes
    assert meta["label"] == "chart"
    assert meta["confidence"] == 0.97
    assert meta["page_number"] == 1


async def test_load_via_extraction_service_returns_none_on_failure():
    client = AsyncMock()
    client.extract = AsyncMock(
        return_value=ExtractResponse(success=False, error="boom")
    )
    backend, _ = _backend(image_store=StubImageStore(), extraction_client=client)

    result = await backend._load_via_extraction_service(
        b"pdf bytes", "report.pdf", {"filename": "report.pdf"}
    )

    assert result is None


# ── ingest() — wires both text and image paths ─────────────────────────────


async def test_ingest_routes_pdf_text_through_pipeline_and_images_through_image_store():
    image_store = StubImageStore()
    client = AsyncMock()
    img_bytes = b"fake-png-bytes"
    client.extract = AsyncMock(
        return_value=ExtractResponse(
            success=True,
            text="page1",
            pages=[ExtractedPageText(page_number=1, text="page1")],
            images=[
                ExtractedImage(
                    data_base64=base64.b64encode(img_bytes).decode("ascii"),
                    page_number=1,
                    label="chart",
                )
            ],
            engine="paddleocr",
            page_count=1,
        )
    )
    client.embed_image = AsyncMock(return_value=[0.1, 0.2])
    backend, pipeline = _backend(image_store=image_store, extraction_client=client)

    result = await backend.ingest(
        b"pdf bytes", collection="kb", metadata={"filename": "report.pdf"}
    )

    assert result.chunks_indexed == 1
    assert len(pipeline.ingested) == 1
    assert len(image_store.documents) == 1


# ── promote() — cheap re-key from staging into a thread's real collection ──


async def test_promote_renames_both_text_and_image_collections():
    vector_store = AsyncMock()
    vector_store.rename_collection = AsyncMock(return_value=2)
    image_store = AsyncMock()
    image_store.rename_collection = AsyncMock(return_value=1)
    backend, _ = _backend(vector_store=vector_store, image_store=image_store)

    moved = await backend.promote(file_id="file-123", thread_id="thread-abc")

    assert moved == 3
    vector_store.rename_collection.assert_awaited_once_with(
        "staging:file-123", "thread-abc"
    )
    image_store.rename_collection.assert_awaited_once_with(
        "staging:file-123", "thread-abc"
    )


async def test_promote_skips_image_store_when_none():
    vector_store = AsyncMock()
    vector_store.rename_collection = AsyncMock(return_value=2)
    backend, _ = _backend(vector_store=vector_store, image_store=None)

    moved = await backend.promote(file_id="file-123", thread_id="thread-abc")

    assert moved == 2


async def test_promote_skips_vector_store_when_none():
    image_store = AsyncMock()
    image_store.rename_collection = AsyncMock(return_value=1)
    backend, _ = _backend(vector_store=None, image_store=image_store)

    moved = await backend.promote(file_id="file-123", thread_id="thread-abc")

    assert moved == 1


async def test_promote_returns_zero_when_neither_store_configured():
    backend, _ = _backend(vector_store=None, image_store=None)

    moved = await backend.promote(file_id="file-123", thread_id="thread-abc")

    assert moved == 0
