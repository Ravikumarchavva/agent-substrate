"""Tests for LocalFilesystemGraphStore — JSON-file-backed graph store."""

from __future__ import annotations

from substrate.agents.storage.local_graph import LocalFilesystemGraphStore
from substrate.kernel.storage.graph import Entity, Relationship


async def test_add_and_get_neighbors_round_trip(tmp_path):
    store = LocalFilesystemGraphStore(root=tmp_path)
    a = Entity(label="Person", id="a")
    b = Entity(label="Person", id="b")
    await store.add_entities([a, b])
    rel = Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")
    await store.add_relationships([rel])

    sub = await store.get_neighbors("a", depth=1)
    assert {e.id for e in sub.entities} == {"a", "b"}
    assert {r.id for r in sub.relationships} == {"r1"}


async def test_delete_entity_cascades_relationships(tmp_path):
    store = LocalFilesystemGraphStore(root=tmp_path)
    await store.add_entities([Entity(label="Person", id="a"), Entity(label="Person", id="b")])
    await store.add_relationships(
        [Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")]
    )

    deleted = await store.delete_entity("a")
    assert deleted is True

    sub = await store.get_neighbors("b", depth=1)
    assert {e.id for e in sub.entities} == {"b"}
    assert sub.relationships == ()


async def test_delete_relationship(tmp_path):
    store = LocalFilesystemGraphStore(root=tmp_path)
    await store.add_entities([Entity(label="Person", id="a"), Entity(label="Person", id="b")])
    await store.add_relationships(
        [Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")]
    )
    assert await store.delete_relationship("r1") is True
    assert await store.delete_relationship("r1") is False

    sub = await store.get_neighbors("a", depth=1)
    assert sub.relationships == ()


async def test_namespace_isolation(tmp_path):
    store = LocalFilesystemGraphStore(root=tmp_path)
    await store.add_entities([Entity(label="Person", id="a")], namespace="tenant1")
    await store.add_entities([Entity(label="Person", id="b")], namespace="tenant2")

    # tenant1 cannot see tenant2's entity
    sub = await store.get_neighbors("a", depth=1, namespace="tenant1")
    assert {e.id for e in sub.entities} == {"a"}

    sub_other = await store.get_neighbors("a", depth=1, namespace="tenant2")
    assert sub_other.entities == ()

    # unscoped namespace sees everything
    sub_all = await store.get_neighbors("a", depth=1, namespace="")
    assert {e.id for e in sub_all.entities} == {"a"}

    # tenant1 cannot delete tenant2's entity
    assert await store.delete_entity("b", namespace="tenant1") is False
    assert await store.delete_entity("b", namespace="tenant2") is True


async def test_get_neighbors_missing_entity_returns_empty(tmp_path):
    store = LocalFilesystemGraphStore(root=tmp_path)
    sub = await store.get_neighbors("nonexistent", depth=1)
    assert sub.entities == ()
    assert sub.relationships == ()


async def test_persistence_across_instances(tmp_path):
    store1 = LocalFilesystemGraphStore(root=tmp_path)
    await store1.add_entities([Entity(label="Person", id="a"), Entity(label="Person", id="b")])
    await store1.add_relationships(
        [Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")]
    )

    # Fresh instance pointed at the same root — simulates a process restart.
    store2 = LocalFilesystemGraphStore(root=tmp_path)
    sub = await store2.get_neighbors("a", depth=1)
    assert {e.id for e in sub.entities} == {"a", "b"}
    assert {r.id for r in sub.relationships} == {"r1"}
