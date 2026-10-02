"""Behaviours of the store's graph beyond the shared conformance suite."""

from __future__ import annotations

from substrate.stores import Store
from substrate.stores import Entity, Relationship


async def test_add_and_get_neighbors_round_trip(tmp_path):
    store = Store.at(tmp_path).graph
    a = Entity(label="Person", id="a")
    b = Entity(label="Person", id="b")
    await store.add_entities([a, b])
    rel = Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")
    await store.add_relationships([rel])

    sub = await store.get_neighbors("a", depth=1)
    assert {e.id for e in sub.entities} == {"a", "b"}
    assert {r.id for r in sub.relationships} == {"r1"}


async def test_delete_entity_cascades_relationships(tmp_path):
    store = Store.at(tmp_path).graph
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
    store = Store.at(tmp_path).graph
    await store.add_entities([Entity(label="Person", id="a"), Entity(label="Person", id="b")])
    await store.add_relationships(
        [Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")]
    )
    assert await store.delete_relationship("r1") is True
    assert await store.delete_relationship("r1") is False

    sub = await store.get_neighbors("a", depth=1)
    assert sub.relationships == ()


async def test_namespace_isolation(tmp_path):
    store = Store.at(tmp_path).graph
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
    store = Store.at(tmp_path).graph
    sub = await store.get_neighbors("nonexistent", depth=1)
    assert sub.entities == ()
    assert sub.relationships == ()


async def test_persistence_across_instances(tmp_path):
    store1 = Store.at(tmp_path).graph
    await store1.add_entities([Entity(label="Person", id="a"), Entity(label="Person", id="b")])
    await store1.add_relationships(
        [Relationship(source_id="a", target_id="b", type="KNOWS", id="r1")]
    )

    # Fresh instance pointed at the same root — simulates a process restart.
    store2 = Store.at(tmp_path).graph
    sub = await store2.get_neighbors("a", depth=1)
    assert {e.id for e in sub.entities} == {"a", "b"}
    assert {r.id for r in sub.relationships} == {"r1"}


async def test_a_deep_traversal_is_one_query_and_a_cycle_does_not_make_it_run_on(tmp_path):
    """A ring of 40 entities, each linked to the next and the last back to the first. Reaching every one of them takes 20
    hops from either side; the walk has to stop when it has seen them all rather than circle."""
    store = Store.at(tmp_path).graph
    n = 40
    await store.add_entities([Entity(id=f"n{i}", label="N") for i in range(n)])
    await store.add_relationships([Relationship(id=f"r{i}", source_id=f"n{i}", target_id=f"n{(i + 1) % n}", type="NEXT") for i in range(n)])

    sub = await store.get_neighbors("n0", depth=100)

    assert len(sub.entities) == n and len(sub.relationships) == n
    assert {e.id for e in (await store.get_neighbors("n0", depth=3)).entities} == {"n0", "n1", "n2", "n3", "n37", "n38", "n39"}


async def test_finding_entities_matches_names_not_syntax(tmp_path):
    store = Store.at(tmp_path).graph
    await store.add_entities(
        [
            Entity(id="e1", label="Company", properties={"name": "Acme Corp"}),
            Entity(id="e2", label="Person", properties={"name": "Wile E. Coyote"}),
            Entity(id="unnamed-widget", label="Thing"),
        ],
    )

    assert [e.id for e in await store.find_entities(["acme"])] == ["e1"]
    assert [e.id for e in await store.find_entities(["COYOTE", "widget"])] == ["e2", "unnamed-widget"]
    assert await store.find_entities(["%"]) == [] and await store.find_entities(["_"]) == []  # LIKE wildcards are literal
    assert await store.find_entities(["'; DROP TABLE graph_entities; --"]) == [] and await store.find_entities([]) == []
    assert len(await store.find_entities(["e"], limit=1)) == 1
