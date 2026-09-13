"""LanceGraphStore — local-path mode (no server needed).

Covers the GraphStore Protocol (add_entities/add_relationships/
get_neighbors/delete_entity/delete_relationship) plus the narrow
CypherCapable.query_cypher — verified against real NetworkX traversal,
not mocked.
"""

from __future__ import annotations

import pytest

from substrate.capabilities.graph.lance_graph_store import LanceGraphStore
from substrate.kernel.storage.graph import CypherCapable, Entity, Relationship


@pytest.fixture
def store(tmp_path) -> LanceGraphStore:
    return LanceGraphStore(path=tmp_path / "graph")


def test_constructor_requires_exactly_one_of_path_or_namespace_uri() -> None:
    with pytest.raises(ValueError):
        LanceGraphStore()
    with pytest.raises(ValueError):
        LanceGraphStore(path="x", namespace_uri="http://x")


def test_is_cypher_capable(store: LanceGraphStore) -> None:
    assert isinstance(store, CypherCapable)


async def test_add_and_get_neighbors_one_hop(store: LanceGraphStore) -> None:
    alice = Entity(id="alice", label="Person", properties={"name": "Alice"})
    acme = Entity(id="acme", label="Company", properties={"name": "Acme"})
    await store.add_entities([alice, acme])
    await store.add_relationships(
        [Relationship(source_id="alice", target_id="acme", type="WORKS_AT")]
    )

    sub = await store.get_neighbors("alice", depth=1)
    assert {e.id for e in sub.entities} == {"acme"}
    assert len(sub.relationships) == 1
    assert sub.relationships[0].type == "WORKS_AT"


async def test_get_neighbors_multi_hop(store: LanceGraphStore) -> None:
    # alice -WORKS_AT-> acme -LOCATED_IN-> nyc
    await store.add_entities(
        [
            Entity(id="alice", label="Person"),
            Entity(id="acme", label="Company"),
            Entity(id="nyc", label="City"),
        ]
    )
    await store.add_relationships(
        [
            Relationship(source_id="alice", target_id="acme", type="WORKS_AT"),
            Relationship(source_id="acme", target_id="nyc", type="LOCATED_IN"),
        ]
    )

    one_hop = await store.get_neighbors("alice", depth=1)
    assert {e.id for e in one_hop.entities} == {"acme"}

    two_hop = await store.get_neighbors("alice", depth=2)
    assert {e.id for e in two_hop.entities} == {"acme", "nyc"}


async def test_get_neighbors_filters_by_relationship_type(
    store: LanceGraphStore,
) -> None:
    await store.add_entities(
        [
            Entity(id="alice", label="Person"),
            Entity(id="acme", label="Company"),
            Entity(id="python", label="Skill"),
        ]
    )
    await store.add_relationships(
        [
            Relationship(source_id="alice", target_id="acme", type="WORKS_AT"),
            Relationship(source_id="alice", target_id="python", type="HAS_SKILL"),
        ]
    )

    sub = await store.get_neighbors("alice", depth=1, relationship_types=["WORKS_AT"])
    assert {e.id for e in sub.entities} == {"acme"}


async def test_get_neighbors_unknown_entity_returns_empty(
    store: LanceGraphStore,
) -> None:
    sub = await store.get_neighbors("nope", depth=1)
    assert sub.entities == () and sub.relationships == ()


async def test_delete_entity_and_relationship(store: LanceGraphStore) -> None:
    await store.add_entities(
        [Entity(id="alice", label="Person"), Entity(id="acme", label="Company")]
    )
    rel_ids = await store.add_relationships(
        [Relationship(source_id="alice", target_id="acme", type="WORKS_AT")]
    )

    assert await store.delete_entity("alice") is True
    assert await store.delete_entity("alice") is False  # already gone

    assert await store.delete_relationship(rel_ids[0]) is True
    assert await store.delete_relationship(rel_ids[0]) is False


async def test_query_cypher_match_all_pattern(store: LanceGraphStore) -> None:
    await store.add_entities(
        [
            Entity(id="alice", label="Person", properties={"name": "Alice"}),
            Entity(id="acme", label="Company", properties={"name": "Acme"}),
        ]
    )

    rows = await store.query_cypher("MATCH (n) RETURN n LIMIT 100")
    assert len(rows) == 2
    import json

    decoded = [json.loads(r["n"]) for r in rows]
    ids = {d["_id"] for d in decoded}
    assert ids == {"alice", "acme"}


async def test_query_cypher_rejects_unsupported_queries(store: LanceGraphStore) -> None:
    with pytest.raises(NotImplementedError):
        await store.query_cypher("MATCH (a)-[r]->(b) RETURN a, r, b")
