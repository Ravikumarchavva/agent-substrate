"""LanceLongTermMemory — local-path mode (no server needed).

Mirrors test_lancedb_store.py's scope note: namespace-catalog mode is
validated against a real running catalog separately, not re-verified here.
"""

from __future__ import annotations

import pytest

from substrate.capabilities.memory.lance_memory_store import LanceLongTermMemory
from substrate.kernel.core.identity import ActorRole, Actor


@pytest.fixture
def store(tmp_path) -> LanceLongTermMemory:
    return LanceLongTermMemory(path=tmp_path / "memories")


AGENT = Actor(role=ActorRole.INTERNAL, id="page_index")


def test_constructor_requires_exactly_one_of_path_or_namespace_uri() -> None:
    with pytest.raises(ValueError):
        LanceLongTermMemory()
    with pytest.raises(ValueError):
        LanceLongTermMemory(path="x", namespace_uri="http://x")


async def test_save_and_search_roundtrip(store: LanceLongTermMemory) -> None:
    await store.save(AGENT, "tree for doc-1", namespace="page_index_trees")
    results = await store.search(AGENT, "doc-1", namespace="page_index_trees")
    assert len(results) == 1
    assert results[0].content == "tree for doc-1"


async def test_search_scoped_by_namespace_and_agent(store: LanceLongTermMemory) -> None:
    other_agent = Actor(role=ActorRole.INTERNAL, id="other")
    await store.save(AGENT, "a", namespace="ns1")
    await store.save(AGENT, "b", namespace="ns2")
    await store.save(other_agent, "c", namespace="ns1")

    results = await store.search(AGENT, "", namespace="ns1")
    assert [m.content for m in results] == ["a"]


async def test_get_and_delete(store: LanceLongTermMemory) -> None:
    memory_id = await store.save(AGENT, "hello", namespace="ns")
    fetched = await store.get(AGENT, memory_id, namespace="ns")
    assert fetched is not None and fetched.content == "hello"

    deleted = await store.delete(AGENT, memory_id, namespace="ns")
    assert deleted is True
    assert await store.get(AGENT, memory_id, namespace="ns") is None


async def test_get_missing_returns_none(store: LanceLongTermMemory) -> None:
    assert await store.get(AGENT, "nope", namespace="ns") is None


async def test_clear_removes_only_matching_namespace(store: LanceLongTermMemory) -> None:
    await store.save(AGENT, "a", namespace="ns1")
    await store.save(AGENT, "b", namespace="ns2")

    await store.clear(AGENT, namespace="ns1")

    assert await store.search(AGENT, "", namespace="ns1") == []
    assert len(await store.search(AGENT, "", namespace="ns2")) == 1


async def test_metadata_roundtrips(store: LanceLongTermMemory) -> None:
    await store.save(AGENT, "x", namespace="ns", metadata={"collection": "brochure"})
    results = await store.search(AGENT, "", namespace="ns")
    assert results[0].metadata == {"collection": "brochure"}
