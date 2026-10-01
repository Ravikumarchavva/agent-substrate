"""Tests for LocalFilesystemVectorStore — JSON-file-backed vector store."""

from __future__ import annotations

import pytest

from substrate.agents.storage.local_vector import LocalFilesystemVectorStore
from substrate.kernel.storage.vector import Document


async def test_add_get_delete_round_trip(tmp_path):
    store = LocalFilesystemVectorStore(root=tmp_path)
    doc = Document.from_text("hello world", id="d1", embedding=[1.0, 0.0])
    ids = await store.add([doc], collection="kb")
    assert ids == ["d1"]

    fetched = await store.get(["d1"], collection="kb")
    assert len(fetched) == 1
    assert fetched[0].to_text() == "hello world"

    removed = await store.delete(["d1"], collection="kb")
    assert removed == 1
    assert await store.get(["d1"], collection="kb") == []


async def test_add_missing_embedding_without_client_raises(tmp_path):
    store = LocalFilesystemVectorStore(root=tmp_path)
    doc = Document.from_text("no vector")
    with pytest.raises(ValueError):
        await store.add([doc], collection="kb")


async def test_upsert_replaces_existing(tmp_path):
    store = LocalFilesystemVectorStore(root=tmp_path)
    doc = Document.from_text("v1", id="d1", embedding=[1.0, 0.0])
    await store.add([doc], collection="kb")

    doc_v2 = Document.from_text("v2", id="d1", embedding=[0.0, 1.0])
    await store.upsert([doc_v2], collection="kb")

    fetched = await store.get(["d1"], collection="kb")
    assert fetched[0].to_text() == "v2"


async def test_search_ranks_by_cosine_similarity(tmp_path):
    store = LocalFilesystemVectorStore(root=tmp_path)
    docs = [
        Document.from_text("close", id="close", embedding=[1.0, 0.0]),
        Document.from_text("far", id="far", embedding=[0.0, 1.0]),
        Document.from_text("opposite", id="opposite", embedding=[-1.0, 0.0]),
    ]
    await store.add(docs, collection="kb")

    results = await store.search([1.0, 0.0], collection="kb", limit=3)
    assert [r.id for r in results] == ["close", "far", "opposite"]
    assert results[0].score == pytest.approx(1.0)


async def test_search_respects_metadata_filter(tmp_path):
    store = LocalFilesystemVectorStore(root=tmp_path)
    docs = [
        Document.from_text("a", id="a", embedding=[1.0, 0.0], metadata={"tag": "x"}),
        Document.from_text("b", id="b", embedding=[1.0, 0.0], metadata={"tag": "y"}),
    ]
    await store.add(docs, collection="kb")

    results = await store.search([1.0, 0.0], collection="kb", filter={"tag": "x"})
    assert [r.id for r in results] == ["a"]


async def test_collections_lifecycle(tmp_path):
    store = LocalFilesystemVectorStore(root=tmp_path)
    await store.add(
        [Document.from_text("a", id="a", embedding=[1.0, 0.0])], collection="kb1"
    )
    await store.add(
        [Document.from_text("b", id="b", embedding=[1.0, 0.0])], collection="kb2"
    )

    collections = await store.list_collections()
    assert set(collections) == {"kb1", "kb2"}

    renamed = await store.rename_collection("kb1", "kb3")
    assert renamed == 1
    collections = await store.list_collections()
    assert set(collections) == {"kb3", "kb2"}
    assert await store.get(["a"], collection="kb3") != []

    deleted = await store.delete_collection("kb2")
    assert deleted == 1
    assert "kb2" not in await store.list_collections()


async def test_persistence_across_instances(tmp_path):
    store1 = LocalFilesystemVectorStore(root=tmp_path)
    await store1.add(
        [Document.from_text("hello", id="d1", embedding=[1.0, 0.0])], collection="kb"
    )

    # Fresh instance pointed at the same root — simulates a process restart.
    store2 = LocalFilesystemVectorStore(root=tmp_path)
    fetched = await store2.get(["d1"], collection="kb")
    assert len(fetched) == 1
    assert fetched[0].to_text() == "hello"

    results = await store2.search([1.0, 0.0], collection="kb")
    assert results[0].id == "d1"
