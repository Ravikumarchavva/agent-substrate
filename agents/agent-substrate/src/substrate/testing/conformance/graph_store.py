"""Conformance suite for ``GraphStore``.

Every implementation — in-memory, local filesystem, Lance, Apache AGE, any a consumer writes — runs
exactly these tests: entities and relationships round-trip through neighbour queries, deleting an entity
takes its edges with it, a namespace is a hard wall between tenants, and hostile ids and names are data.
Subclass it and provide the ``store`` fixture.
"""

from __future__ import annotations

import uuid

import pytest

from substrate.stores.graph import Entity, GraphStore, Relationship


def ids(sub) -> set[str]:
    return {e.id for e in sub.entities}


class GraphStoreConformance:
    @pytest.fixture
    async def store(self) -> GraphStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @pytest.fixture
    def ns(self) -> str:
        """A namespace no earlier test used: a database outlives a test."""
        return f"ns-{uuid.uuid4().hex[:12]}"

    async def people(self, store: GraphStore, ns: str) -> None:
        """a -KNOWS-> b -KNOWS-> c, and a -WORKS_AT-> acme."""
        await store.add_entities(
            [
                Entity(id="a", label="Person", properties={"name": "Ann"}),
                Entity(id="b", label="Person", properties={"name": "Bob"}),
                Entity(id="c", label="Person", properties={"name": "Cy"}),
                Entity(id="acme", label="Company", properties={"name": "Acme"}),
            ],
            namespace=ns,
        )
        await store.add_relationships(
            [
                Relationship(id="r1", source_id="a", target_id="b", type="KNOWS"),
                Relationship(id="r2", source_id="b", target_id="c", type="KNOWS"),
                Relationship(
                    id="r3",
                    source_id="a",
                    target_id="acme",
                    type="WORKS_AT",
                    properties={"since": 2020},
                ),
            ],
            namespace=ns,
        )

    async def test_added_entities_and_relationships_return_their_ids(
        self, store: GraphStore, ns: str
    ) -> None:
        assert sorted(
            await store.add_entities(
                [Entity(id="x", label="T"), Entity(id="y", label="T")], namespace=ns
            )
        ) == ["x", "y"]
        assert await store.add_relationships(
            [Relationship(id="rel", source_id="x", target_id="y", type="LINKS")],
            namespace=ns,
        ) == ["rel"]

    async def test_one_hop_neighbours_include_the_edges_between_them(
        self, store: GraphStore, ns: str
    ) -> None:
        await self.people(store, ns)
        sub = await store.get_neighbors("a", namespace=ns)
        assert {"b", "acme"} <= ids(sub) and "c" not in ids(sub)
        assert {r.id for r in sub.relationships} >= {"r1", "r3"}
        works = next(r for r in sub.relationships if r.id == "r3")
        assert works.type == "WORKS_AT" and works.properties == {"since": 2020}

    async def test_depth_reaches_further(self, store: GraphStore, ns: str) -> None:
        await self.people(store, ns)
        assert "c" in ids(await store.get_neighbors("a", depth=2, namespace=ns))
        assert "c" not in ids(await store.get_neighbors("a", depth=1, namespace=ns))

    async def test_relationship_types_filter_the_traversal(
        self, store: GraphStore, ns: str
    ) -> None:
        await self.people(store, ns)
        sub = await store.get_neighbors(
            "a", relationship_types=["WORKS_AT"], namespace=ns
        )
        assert "acme" in ids(sub) and "b" not in ids(sub)

    async def test_neighbours_of_an_unknown_entity_are_empty(
        self, store: GraphStore, ns: str
    ) -> None:
        sub = await store.get_neighbors("nobody", namespace=ns)
        assert sub.relationships == () and "nobody" not in ids(sub) - {"nobody"}

    async def test_deleting_an_entity_removes_it_and_its_relationships(
        self, store: GraphStore, ns: str
    ) -> None:
        await self.people(store, ns)
        assert await store.delete_entity("b", namespace=ns) is True
        sub = await store.get_neighbors("a", depth=2, namespace=ns)
        assert "b" not in ids(sub) and "c" not in ids(sub)
        assert all("b" not in (r.source_id, r.target_id) for r in sub.relationships)
        assert await store.delete_entity("b", namespace=ns) is False

    async def test_deleting_a_relationship_leaves_the_entities(
        self, store: GraphStore, ns: str
    ) -> None:
        await self.people(store, ns)
        assert await store.delete_relationship("r1", namespace=ns) is True
        sub = await store.get_neighbors("a", namespace=ns)
        assert "b" not in ids(sub) and "acme" in ids(sub)
        assert await store.delete_relationship("r1", namespace=ns) is False

    async def test_namespaces_are_a_hard_wall(self, store: GraphStore, ns: str) -> None:
        other = ns + "-other"
        await self.people(store, ns)
        assert (
            ids(await store.get_neighbors("a", namespace=other)) <= {"a"}
            and (await store.get_neighbors("a", namespace=other)).relationships == ()
        )
        assert await store.delete_entity("a", namespace=other) is False
        assert await store.delete_relationship("r1", namespace=other) is False
        assert "b" in ids(await store.get_neighbors("a", namespace=ns)), (
            "another namespace's delete reached this one"
        )

    async def test_the_same_id_in_two_namespaces_is_two_entities(
        self, store: GraphStore, ns: str
    ) -> None:
        other = ns + "-other"
        await store.add_entities(
            [Entity(id="e", label="One", properties={"v": 1})], namespace=ns
        )
        await store.add_entities(
            [Entity(id="e", label="Two", properties={"v": 2})], namespace=other
        )
        await store.add_entities([Entity(id="f", label="F")], namespace=ns)
        await store.add_relationships(
            [Relationship(id="rr", source_id="e", target_id="f", type="T")],
            namespace=ns,
        )
        mine = (await store.get_neighbors("e", namespace=ns)).entities
        assert {e.id: e.label for e in mine}.get("e") in (None, "One"), (
            "this namespace saw the other's entity"
        )
        assert "f" not in ids(await store.get_neighbors("e", namespace=other))

    @pytest.mark.parametrize(
        "hostile",
        [
            "x' OR '1'='1",
            "a'; DROP TABLE t; --",
            "../../etc/passwd",
            "ünï-çødé",
            'q"uote',
            "back\\slash",
        ],
    )
    async def test_hostile_ids_labels_and_types_are_inert(
        self, store: GraphStore, ns: str, hostile: str
    ) -> None:
        await self.people(store, ns)
        await store.add_entities(
            [Entity(id=hostile, label="Person", properties={"name": hostile})],
            namespace=ns,
        )
        await store.add_relationships(
            [
                Relationship(
                    id=f"rel-{uuid.uuid4().hex[:6]}",
                    source_id="a",
                    target_id=hostile,
                    type="KNOWS",
                )
            ],
            namespace=ns,
        )
        assert hostile in ids(await store.get_neighbors("a", namespace=ns))
        assert await store.delete_entity(hostile, namespace=ns) is True
        sub = await store.get_neighbors("a", depth=2, namespace=ns)
        assert {"b", "c", "acme"} <= ids(sub), "a hostile id damaged other entities"


__all__ = ["GraphStoreConformance"]
