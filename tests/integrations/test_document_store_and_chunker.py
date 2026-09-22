"""Tests for DocumentStore and ExtractionDocumentChunker implementations."""

from __future__ import annotations

import pytest

from substrate.integrations.knowledge.chunking import (
    ExtractionDocumentChunker,
    get_chunker,
)
from substrate.integrations.knowledge.document_store import InMemoryDocumentStore
from substrate.kernel.document.models import (
    DocumentChunk,
    DocumentMetadata,
    ExtractedPage,
    ExtractionResult,
)
from substrate.kernel.document.protocols import DocumentChunker, DocumentStore


def test_extraction_document_chunker_implements_protocol():
    chunker = ExtractionDocumentChunker()
    assert isinstance(chunker, DocumentChunker)


def test_extraction_document_chunker_chunks_pages():
    chunker = ExtractionDocumentChunker(strategy="structure")
    page1 = ExtractedPage(
        page_number=1,
        text="Introduction text here.",
        markdown="# Introduction\n\nThis is the introductory section of the document.",
        metadata={"author": "Alice"},
    )
    page2 = ExtractedPage(
        page_number=2,
        text="Section two text.",
        markdown="## Details\n\nHere are some comprehensive details on page two.",
    )
    result = ExtractionResult(
        success=True,
        pages=[page1, page2],
        engine="test_engine",
    )

    chunks = chunker.chunk(result, chunk_size=100, chunk_overlap=20)
    assert len(chunks) >= 2
    assert all(isinstance(c, DocumentChunk) for c in chunks)

    # Check page 1 chunk
    assert chunks[0].page_number == 1
    assert "Introduction" in chunks[0].text
    assert chunks[0].metadata.get("page_number") == 1
    assert chunks[0].metadata.get("author") == "Alice"

    # Check page 2 chunk
    assert chunks[-1].page_number == 2
    assert "Details" in chunks[-1].text


def test_extraction_document_chunker_markdown_fallback():
    chunker = get_chunker("extraction", strategy="text")
    result = ExtractionResult(
        success=True,
        pages=[],
        markdown="A document with only markdown content and no individual page structures.",
        engine="markdown_only",
    )

    chunks = chunker.chunk(result, chunk_size=50, chunk_overlap=10)
    assert len(chunks) >= 1
    assert all(c.page_number is None for c in chunks)


@pytest.mark.asyncio
async def test_in_memory_document_store():
    store = InMemoryDocumentStore()
    assert isinstance(store, DocumentStore)

    meta = DocumentMetadata(
        id="doc-123",
        filename="report.pdf",
        content_type="application/pdf",
        byte_size=1024,
        total_pages=2,
    )
    chunks = [
        DocumentChunk.from_text("Chunk 1 text", document_id="doc-123", page_number=1, chunk_index=0),
        DocumentChunk.from_text("Chunk 2 text", document_id="doc-123", page_number=2, chunk_index=1),
    ]

    await store.save_document(meta, chunks)

    fetched_meta = await store.get_document("doc-123")
    assert fetched_meta is not None
    assert fetched_meta.filename == "report.pdf"

    fetched_chunks = await store.get_chunks("doc-123")
    assert len(fetched_chunks) == 2
    assert fetched_chunks[0].text == "Chunk 1 text"
    assert fetched_chunks[1].page_number == 2

    docs = await store.list_documents(limit=10)
    assert len(docs) == 1
    assert docs[0].id == "doc-123"

    await store.delete_document("doc-123")
    assert await store.get_document("doc-123") is None
    assert await store.get_chunks("doc-123") == []

