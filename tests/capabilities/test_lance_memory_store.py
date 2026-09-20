"""LanceMemoryStore — local-path mode (no server needed).

Mirrors test_lancedb_store.py's scope note: namespace-catalog mode is
validated against a real running catalog separately, not re-verified here.
"""

from __future__ import annotations

import pytest

from substrate.capabilities.memory.lance_memory_store import LanceMemoryStore
from substrate.kernel.storage.memory import (
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)


@pytest.fixture
def store(tmp_path) -> LanceMemoryStore:
    return LanceMemoryStore(path=tmp_path / "memories")


def test_constructor_requires_exactly_one_of_path_or_namespace_uri() -> None:
    with pytest.raises(ValueError):
        LanceMemoryStore()
    with pytest.raises(ValueError):
        LanceMemoryStore(path="x", namespace_uri="http://x")


async def test_save_and_query_roundtrip(store: LanceMemoryStore) -> None:
    ns = MemoryNamespace(tenant_id="page_index_trees", agent_id="page_index")
    rec = MemoryRecord.from_text("tree for doc-1", namespace=ns)
    await store.save(rec)

    matches = await store.query(MemoryQuery(namespace=ns, text_query="doc-1"))
    assert len(matches) == 1
    assert matches[0].record.to_text() == "tree for doc-1"


async def test_query_scoped_by_namespace_and_agent(store: LanceMemoryStore) -> None:
    ns1 = MemoryNamespace(tenant_id="ns1", agent_id="agent1")
    ns2 = MemoryNamespace(tenant_id="ns2", agent_id="agent1")
    ns3 = MemoryNamespace(tenant_id="ns1", agent_id="other")

    await store.save(MemoryRecord.from_text("a", namespace=ns1))
    await store.save(MemoryRecord.from_text("b", namespace=ns2))
    await store.save(MemoryRecord.from_text("c", namespace=ns3))

    results = await store.query(MemoryQuery(namespace=ns1))
    assert [m.record.to_text() for m in results] == ["a"]


async def test_get_and_delete(store: LanceMemoryStore) -> None:
    ns = MemoryNamespace(tenant_id="ns", agent_id="agent1")
    rec = MemoryRecord.from_text("hello", namespace=ns)
    memory_id = await store.save(rec)

    fetched = await store.get(memory_id)
    assert fetched is not None and fetched.to_text() == "hello"

    deleted = await store.delete(memory_id)
    assert deleted is True
    assert await store.get(memory_id) is None


async def test_get_missing_returns_none(store: LanceMemoryStore) -> None:
    assert await store.get("nope") is None


async def test_clear_removes_only_matching_namespace(store: LanceMemoryStore) -> None:
    ns1 = MemoryNamespace(tenant_id="ns1", agent_id="agent1")
    ns2 = MemoryNamespace(tenant_id="ns2", agent_id="agent1")

    await store.save(MemoryRecord.from_text("a", namespace=ns1))
    await store.save(MemoryRecord.from_text("b", namespace=ns2))

    await store.clear(ns1)

    assert await store.query(MemoryQuery(namespace=ns1)) == []
    assert len(await store.query(MemoryQuery(namespace=ns2))) == 1


async def test_metadata_roundtrips(store: LanceMemoryStore) -> None:
    ns = MemoryNamespace(tenant_id="ns", agent_id="agent1")
    rec = MemoryRecord.from_text("x", namespace=ns, metadata={"collection": "brochure"})
    await store.save(rec)

    results = await store.query(MemoryQuery(namespace=ns))
    assert results[0].record.metadata == {"collection": "brochure"}


async def test_multimodal_blocks_roundtrip(store: LanceMemoryStore) -> None:
    from substrate.kernel.core.content import DataBlock, MediaBlock, TextBlock

    blocks = [
        TextBlock(text="Summary of invoice"),
        MediaBlock.image(url="https://example.com/inv.png", media_type="image/png"),
        DataBlock(data={"total": 1500, "currency": "USD"}),
    ]
    ns = MemoryNamespace(tenant_id="multimodal_ns", agent_id="agent1")
    rec = MemoryRecord(content=blocks, namespace=ns)
    mem_id = await store.save(rec)

    fetched = await store.get(mem_id)
    assert fetched is not None
    assert len(fetched.content) == 3
    assert isinstance(fetched.content[0], TextBlock)
    assert isinstance(fetched.content[1], MediaBlock)
    assert fetched.content[1].type == "image"
    assert isinstance(fetched.content[2], DataBlock)
    assert fetched.content[2].data == {"total": 1500, "currency": "USD"}
    assert "Summary of invoice" in fetched.to_text()
