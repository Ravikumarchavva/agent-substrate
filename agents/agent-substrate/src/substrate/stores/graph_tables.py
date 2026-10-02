"""Graph — entities and relationships, kept in the store's database.

``Graph`` is the one implementation of ``GraphStore`` (``stores/graph.py``). Entities and relationships are rows; a
neighbourhood is one recursive query over the relationship table, so a traversal costs a query however deep it goes,
not a read per hop.

``namespace`` is part of a row's identity: the same id in two namespaces is two entities, and a call that names a
namespace sees and changes only that namespace's rows. The empty namespace is the contract's "unscoped" — it addresses
the whole graph — so a host that wants a wall binds the store to a tenant instead of passing ``""``.

Traversal treats relationships as undirected, one hop per level up to ``depth``, optionally only along some types.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from substrate.stores.database import Row, Tx, under
from substrate.stores.graph import Entity, Relationship, SubGraph

if TYPE_CHECKING:
    from substrate.stores.store import Store

T = TypeVar("T")

SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS graph_entities (
    seq {pk},
    namespace TEXT NOT NULL,
    id TEXT NOT NULL,
    label TEXT NOT NULL,
    name TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    UNIQUE (namespace, id)
);
CREATE INDEX IF NOT EXISTS graph_entities_id_idx ON graph_entities (id);

CREATE TABLE IF NOT EXISTS graph_relationships (
    seq {pk},
    namespace TEXT NOT NULL,
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    type TEXT NOT NULL,
    properties_json TEXT NOT NULL,
    UNIQUE (namespace, id)
);
CREATE INDEX IF NOT EXISTS graph_relationships_source_idx ON graph_relationships (source_id);
CREATE INDEX IF NOT EXISTS graph_relationships_target_idx ON graph_relationships (target_id);
"""
]


def _entity(row: Row) -> Entity:
    return Entity(
        id=row["id"], label=row["label"], properties=json.loads(row["properties_json"])
    )


def _relationship(row: Row) -> Relationship:
    return Relationship(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        type=row["type"],
        properties=json.loads(row["properties_json"]),
    )


def _in_namespace(namespace: str, alias: str = "") -> tuple[str, list[str]]:
    """The namespace rule as SQL: a named namespace sees only its own rows, the empty one sees them all."""
    column = f"{alias}.namespace" if alias else "namespace"
    return f"(? = '' OR {column} = ?)", [namespace, namespace]


def _like(term: str) -> str:
    return (
        "%"
        + term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )


class Graph:
    """The ``GraphStore`` of a ``Store``: ``store.graph``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        """The store this graph lives in — for a host that opened it and has to close it."""
        return self._store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        return await self._store.run(fn)

    async def add_entities(
        self, entities: list[Entity], *, namespace: str = ""
    ) -> list[str]:
        async def op(tx: Tx) -> list[str]:
            for entity in entities:
                name = entity.properties.get("name")
                await tx.execute(
                    "INSERT INTO graph_entities (namespace, id, label, name, properties_json) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (namespace, id) DO UPDATE SET label = excluded.label, name = excluded.name, "
                    "properties_json = excluded.properties_json",
                    namespace,
                    entity.id,
                    entity.label,
                    name if isinstance(name, str) else "",
                    json.dumps(entity.properties),
                )
            return [e.id for e in entities]

        return await self._run(op)

    async def add_relationships(
        self, relationships: list[Relationship], *, namespace: str = ""
    ) -> list[str]:
        async def op(tx: Tx) -> list[str]:
            for rel in relationships:
                await tx.execute(
                    "INSERT INTO graph_relationships (namespace, id, source_id, target_id, type, properties_json) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (namespace, id) DO UPDATE SET source_id = excluded.source_id, "
                    "target_id = excluded.target_id, type = excluded.type, properties_json = excluded.properties_json",
                    namespace,
                    rel.id,
                    rel.source_id,
                    rel.target_id,
                    rel.type,
                    json.dumps(rel.properties),
                )
            return [r.id for r in relationships]

        return await self._run(op)

    async def delete_entity(self, entity_id: str, *, namespace: str = "") -> bool:
        """Delete the entity and, with it, every relationship that touches it (``DETACH DELETE``)."""

        async def op(tx: Tx) -> bool:
            visible, params = _in_namespace(namespace)
            deleted = await tx.execute(
                f"DELETE FROM graph_entities WHERE id = ? AND {visible}",
                entity_id,
                *params,
            )
            if deleted == 0:
                return False
            await tx.execute(
                f"DELETE FROM graph_relationships WHERE (source_id = ? OR target_id = ?) AND {visible}",
                entity_id,
                entity_id,
                *params,
            )
            return True

        return await self._run(op)

    async def delete_relationship(
        self, relationship_id: str, *, namespace: str = ""
    ) -> bool:
        async def op(tx: Tx) -> bool:
            visible, params = _in_namespace(namespace)
            return (
                await tx.execute(
                    f"DELETE FROM graph_relationships WHERE id = ? AND {visible}",
                    relationship_id,
                    *params,
                )
                > 0
            )

        return await self._run(op)

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
        namespace: str = "",
    ) -> SubGraph:
        async def op(tx: Tx) -> SubGraph:
            visible, ns = _in_namespace(namespace)
            if (
                await tx.fetchone(
                    f"SELECT 1 FROM graph_entities WHERE id = ? AND {visible}",
                    entity_id,
                    *ns,
                )
                is None
            ):
                return SubGraph()

            types = list(relationship_types) if relationship_types else []
            type_clause = (
                f" AND r.type IN ({', '.join('?' for _ in types)})" if types else ""
            )
            rel_ns, rel_params = _in_namespace(namespace, "r")
            # The nodes within ``depth`` hops, each with the fewest hops it took to reach it. UNION (not UNION ALL)
            # drops repeated (node, hops) pairs, so a cycle cannot make the walk run on.
            reach = (
                "WITH RECURSIVE reach(id, depth) AS ("
                " SELECT ?, 0"
                " UNION"
                " SELECT CASE WHEN r.source_id = reach.id THEN r.target_id ELSE r.source_id END, reach.depth + 1"
                " FROM reach JOIN graph_relationships r ON (r.source_id = reach.id OR r.target_id = reach.id)"
                f" WHERE reach.depth < ? AND {rel_ns}{type_clause}"
                "), near AS (SELECT id FROM reach GROUP BY id HAVING MIN(depth) < ?)"
            )
            reach_params: list[object] = [entity_id, depth, *rel_params, *types, depth]

            entity_ns, entity_params = _in_namespace(namespace, "e")
            entities = await tx.fetchall(
                f"{reach} SELECT e.* FROM graph_entities e WHERE e.id IN (SELECT id FROM reach) AND {entity_ns} ORDER BY e.seq",
                *reach_params,
                *entity_params,
            )
            relationships = await tx.fetchall(
                f"{reach} SELECT r.* FROM graph_relationships r WHERE {rel_ns}{type_clause} "
                "AND (r.source_id IN (SELECT id FROM near) OR r.target_id IN (SELECT id FROM near)) ORDER BY r.seq",
                *reach_params,
                *rel_params,
                *types,
            )
            return SubGraph(
                entities=tuple(_entity(row) for row in entities),
                relationships=tuple(_relationship(row) for row in relationships),
            )

        return await self._run(op)

    async def find_entities(
        self, terms: Sequence[str], *, limit: int = 100, namespace: str = ""
    ) -> list[Entity]:
        """Entities whose name — or id, for one with no name — contains any of ``terms``, case-insensitively."""
        terms = [t for t in terms if t]
        if not terms:
            return []

        async def op(tx: Tx) -> list[Entity]:
            visible, ns = _in_namespace(namespace)
            match = " OR ".join(
                "LOWER(CASE WHEN name <> '' THEN name ELSE id END) LIKE ? ESCAPE '\\'"
                for _ in terms
            )
            rows = await tx.fetchall(
                f"SELECT * FROM graph_entities WHERE {visible} AND ({match}) ORDER BY seq LIMIT ?",
                *ns,
                *[_like(t) for t in terms],
                limit,
            )
            return [_entity(row) for row in rows]

        return await self._run(op)

    async def erase_under(self, name: str) -> int:
        """Delete every entity and relationship in the namespace ``name`` or below it. Returns the entities removed."""
        clause, params = under("namespace", name)

        async def op(tx: Tx) -> int:
            await tx.execute(f"DELETE FROM graph_relationships WHERE {clause}", *params)
            return await tx.execute(
                f"DELETE FROM graph_entities WHERE {clause}", *params
            )

        erased = await self._run(op)
        if erased:
            await self._store.database.reclaim()
        return erased


__all__ = ["SCHEMA", "Graph"]
