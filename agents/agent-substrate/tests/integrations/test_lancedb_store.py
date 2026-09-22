"""LanceDBVectorStore — local-path mode (no server needed).

Namespace-catalog mode (SeaweedFS's Lance Catalog) is validated separately
against a real running catalog — see the Phase 0 spike notes in the storage
plan; it isn't re-verified here since standing one up is out of scope for
the unit suite.
"""

from __future__ import annotations

import pytest

from substrate.capabilities.vector.lancedb_store import LanceDBVectorStore
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import Document


@pytest.fixture
def store(tmp_path) -> LanceDBVectorStore:
    return LanceDBVectorStore(path=tmp_path / "lancedb")


def _doc(id: str, text: str, vector: list[float], **metadata) -> Document:
    return Document(
        id=id, content=[TextBlock(text=text)], embedding=vector, metadata=metadata
    )


def test_constructor_requires_exactly_one_of_path_or_namespace_uri() -> None:
    with pytest.raises(ValueError):
        LanceDBVectorStore()
    with pytest.raises(ValueError):
        LanceDBVectorStore(path="x", namespace_uri="http://x")


def test_namespace_uri_requires_namespace_path() -> None:
    with pytest.raises(ValueError):
        LanceDBVectorStore(namespace_uri="http://x")


async def test_add_and_search_roundtrip(store: LanceDBVectorStore) -> None:
    await store.add(
        [
            _doc("a", "cats are great pets", [1.0, 0.0]),
            _doc("b", "stock market crashed today", [0.0, 1.0]),
        ],
        collection="c1",
    )
    results = await store.search([1.0, 0.0], collection="c1", limit=1)
    assert len(results) == 1
    assert results[0].id == "a"


async def test_search_missing_collection_returns_empty(store: LanceDBVectorStore) -> None:
    assert await store.search([1.0, 0.0], collection="nope") == []


async def test_get_and_delete(store: LanceDBVectorStore) -> None:
    await store.add([_doc("a", "hello", [1.0, 0.0])], collection="c1")
    docs = await store.get(["a"], collection="c1")
    assert len(docs) == 1 and docs[0].id == "a"

    deleted = await store.delete(["a"], collection="c1")
    assert deleted == 1
    assert await store.get(["a"], collection="c1") == []


async def test_hybrid_search_fuses_vector_and_text(store: LanceDBVectorStore) -> None:
    await store.add(
        [
            _doc("a", "cats are great pets", [1.0, 0.0]),
            _doc("b", "dogs are great pets too", [0.9, 0.1]),
            _doc("c", "stock market crashed today", [0.0, 1.0]),
        ],
        collection="c1",
    )
    results = await store.hybrid_search([1.0, 0.0], "great pets", collection="c1")
    assert [r.id for r in results][:2] == ["a", "b"]


async def test_hybrid_search_respects_metadata_filter(store: LanceDBVectorStore) -> None:
    await store.add(
        [
            _doc("a", "great pets", [1.0, 0.0], kind="animal"),
            _doc("b", "great pets", [1.0, 0.0], kind="plant"),
        ],
        collection="c1",
    )
    results = await store.hybrid_search(
        [1.0, 0.0], "great pets", collection="c1", filter={"kind": "animal"}
    )
    assert [r.id for r in results] == ["a"]


async def test_hybrid_search_missing_collection_returns_empty(
    store: LanceDBVectorStore,
) -> None:
    assert await store.hybrid_search([1.0, 0.0], "text", collection="nope") == []


async def test_list_delete_rename_collections(store: LanceDBVectorStore) -> None:
    await store.add([_doc("a", "x", [1.0, 0.0])], collection="c1")
    assert await store.list_collections() == ["c1"]

    moved = await store.rename_collection("c1", "c2")
    assert moved == 1
    assert await store.list_collections() == ["c2"]

    deleted = await store.delete_collection("c2")
    assert deleted == 1
    assert await store.list_collections() == []
