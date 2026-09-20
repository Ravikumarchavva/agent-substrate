"""In-memory graph store for local development and tests (L1).

A dependency-free :class:`~substrate.kernel.storage.graph.GraphStore` implementation
backed by plain dicts. ``get_neighbors`` does a breadth-first traversal up to
``depth`` hops over undirected edges (matching the AGE store's ``-[r]-`` pattern),
optionally filtered by relationship type.

It intentionally does **not** implement ``CypherCapable`` — there is no Cypher
engine here, so ``isinstance(store, CypherCapable)`` correctly returns ``False``.

Usage::

    from substrate.agents.storage import InMemoryGraphStore

    store = InMemoryGraphStore()
    await store.add_entities([Entity(label="Person", id="p1")])
    sub = await store.get_neighbors("p1", depth=2)
"""

from __future__ import annotations

from collections import deque

from substrate.kernel.storage.graph import Entity, Relationship, SubGraph


class InMemoryGraphStore:
    """Dict-backed GraphStore with BFS neighbor traversal.

    Every entity/relationship remembers the namespace it was written under.
    ``namespace=""`` sees the whole graph; a non-empty namespace sees only what
    was written under it — the same contract as the Lance and AGE stores.
    """

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._relationships: dict[str, Relationship] = {}
        self._namespace_of: dict[str, str] = {}  # entity/relationship id -> namespace

    def _visible(self, item_id: str, namespace: str) -> bool:
        return not namespace or self._namespace_of.get(item_id) == namespace

    # ── Write ──────────────────────────────────────────────────────────────

    async def add_entities(
        self, entities: list[Entity], *, namespace: str = ""
    ) -> list[str]:
        for entity in entities:
            self._entities[entity.id] = entity
            self._namespace_of[entity.id] = namespace
        return [e.id for e in entities]

    async def add_relationships(
        self, relationships: list[Relationship], *, namespace: str = ""
    ) -> list[str]:
        for rel in relationships:
            self._relationships[rel.id] = rel
            self._namespace_of[rel.id] = namespace
        return [r.id for r in relationships]

    async def delete_entity(self, entity_id: str, *, namespace: str = "") -> bool:
        if entity_id not in self._entities or not self._visible(entity_id, namespace):
            return False
        del self._entities[entity_id]
        self._namespace_of.pop(entity_id, None)
        # DETACH DELETE: drop every visible relationship touching this entity.
        for rid, rel in list(self._relationships.items()):
            if entity_id in (rel.source_id, rel.target_id) and self._visible(
                rid, namespace
            ):
                del self._relationships[rid]
                self._namespace_of.pop(rid, None)
        return True

    async def delete_relationship(
        self, relationship_id: str, *, namespace: str = ""
    ) -> bool:
        if relationship_id not in self._relationships or not self._visible(
            relationship_id, namespace
        ):
            return False
        del self._relationships[relationship_id]
        self._namespace_of.pop(relationship_id, None)
        return True

    # ── Read ───────────────────────────────────────────────────────────────

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
        namespace: str = "",
    ) -> SubGraph:
        entities = {
            i: e for i, e in self._entities.items() if self._visible(i, namespace)
        }
        relationships = {
            i: r for i, r in self._relationships.items() if self._visible(i, namespace)
        }
        if entity_id not in entities:
            return SubGraph()

        type_filter: set[str] | None = (
            set(relationship_types) if relationship_types else None
        )
        visited_entities: dict[str, Entity] = {entity_id: entities[entity_id]}
        traversed_rels: dict[str, Relationship] = {}

        # BFS over undirected edges, expanding one hop per level up to *depth*.
        frontier: deque[tuple[str, int]] = deque([(entity_id, 0)])
        seen: set[str] = {entity_id}
        while frontier:
            current, hops = frontier.popleft()
            if hops >= depth:
                continue
            for rel in relationships.values():
                if type_filter is not None and rel.type not in type_filter:
                    continue
                if rel.source_id == current:
                    neighbor = rel.target_id
                elif rel.target_id == current:
                    neighbor = rel.source_id
                else:
                    continue

                traversed_rels[rel.id] = rel
                entity = entities.get(neighbor)
                if entity is not None:
                    visited_entities[neighbor] = entity
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append((neighbor, hops + 1))

        return SubGraph(
            entities=tuple(visited_entities.values()),
            relationships=tuple(traversed_rels.values()),
        )


__all__ = ["InMemoryGraphStore"]
