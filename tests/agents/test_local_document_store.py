"""LocalFilesystemDocumentStore — the L1 default DocumentStore."""

from __future__ import annotations

from pathlib import Path

from substrate.agents.document import LocalFilesystemDocumentStore
from substrate.kernel.document import DocumentChunk, DocumentMetadata, DocumentStore


async def test_round_trip_list_and_delete(tmp_path: Path):
    store = LocalFilesystemDocumentStore(tmp_path)
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

    fetched = await store.get_document("doc-123")
    assert fetched is not None and fetched.filename == "report.pdf"
    fetched_chunks = await store.get_chunks("doc-123")
    assert [c.text for c in fetched_chunks] == ["Chunk 1 text", "Chunk 2 text"]
    assert fetched_chunks[1].page_number == 2
    assert [d.id for d in await store.list_documents(limit=10)] == ["doc-123"]

    await store.delete_document("doc-123")
    assert await store.get_document("doc-123") is None
    assert await store.get_chunks("doc-123") == []
    assert await store.list_documents() == []


async def test_survives_a_restart(tmp_path: Path):
    meta = DocumentMetadata(id="d1", filename="a.txt")
    await LocalFilesystemDocumentStore(tmp_path).save_document(
        meta, [DocumentChunk.from_text("hello", document_id="d1")]
    )

    reopened = LocalFilesystemDocumentStore(tmp_path)
    assert (await reopened.get_document("d1")).filename == "a.txt"  # type: ignore[union-attr]
    assert [c.text for c in await reopened.get_chunks("d1")] == ["hello"]


async def test_listing_is_paged_oldest_first(tmp_path: Path):
    store = LocalFilesystemDocumentStore(tmp_path)
    for i in range(5):
        await store.save_document(DocumentMetadata(id=f"d{i}", filename=f"{i}.txt"), [])

    assert [d.id for d in await store.list_documents(limit=2, offset=1)] == ["d1", "d2"]


async def test_a_hostile_document_id_stays_inside_the_root(tmp_path: Path):
    store = LocalFilesystemDocumentStore(tmp_path / "store")
    await store.save_document(DocumentMetadata(id="../../evil", filename="x"), [])

    assert (await store.get_document("../../evil")) is not None
    assert all(p.resolve().is_relative_to((tmp_path / "store").resolve()) for p in tmp_path.rglob("*"))
    assert not (tmp_path.parent / "evil").exists()
