"""In-memory graph store for local development and tests (L1).

A dependency-free :class:`~agent_substrate.kernel.storage.graph.GraphStore` implementation
backed by plain dicts. ``get_neighbors`` does a breadth-first traversal up to
``depth`` hops over undirected edges (matching the AGE store's ``-[r]-`` pattern),
optionally filtered by relationship type.

It intentionally does **not** implement ``CypherCapable`` — there is no Cypher
engine here, so ``isinstance(store, CypherCapable)`` correctly returns ``False``.

Usage::

    from agent_substrate.agents.storage import InMemoryGraphStore

    store = InMemoryGraphStore()
    await store.add_entities([Entity(label="Person", id="p1")])
    sub = await store.get_neighbors("p1", depth=2)
"""

from __future__ import annotations

from collections import deque

from agent_substrate.kernel.storage.graph import Entity, Relationship, SubGraph


class InMemoryGraphStore:
    """Dict-backed GraphStore with BFS neighbor traversal."""

    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._relationships: dict[str, Relationship] = {}

    # ── Write ──────────────────────────────────────────────────────────────

    async def add_entities(self, entities: list[Entity]) -> list[str]:
        ids: list[str] = []
        for entity in entities:
            self._entities[entity.id] = entity
            ids.append(entity.id)
        return ids

    async def add_relationships(self, relationships: list[Relationship]) -> list[str]:
        ids: list[str] = []
        for rel in relationships:
            self._relationships[rel.id] = rel
            ids.append(rel.id)
        return ids

    async def delete_entity(self, entity_id: str) -> bool:
        if entity_id not in self._entities:
            return False
        del self._entities[entity_id]
        # DETACH DELETE: drop every relationship touching this entity.
        self._relationships = {
            rid: rel
            for rid, rel in self._relationships.items()
            if rel.source_id != entity_id and rel.target_id != entity_id
        }
        return True

    async def delete_relationship(self, relationship_id: str) -> bool:
        return self._relationships.pop(relationship_id, None) is not None

    # ── Read ───────────────────────────────────────────────────────────────

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
    ) -> SubGraph:
        if entity_id not in self._entities:
            return SubGraph()

        type_filter: set[str] | None = (
            set(relationship_types) if relationship_types else None
        )
        visited_entities: dict[str, Entity] = {entity_id: self._entities[entity_id]}
        traversed_rels: dict[str, Relationship] = {}

        # BFS over undirected edges, expanding one hop per level up to *depth*.
        frontier: deque[tuple[str, int]] = deque([(entity_id, 0)])
        seen: set[str] = {entity_id}
        while frontier:
            current, hops = frontier.popleft()
            if hops >= depth:
                continue
            for rel in self._relationships.values():
                if type_filter is not None and rel.type not in type_filter:
                    continue
                if rel.source_id == current:
                    neighbor = rel.target_id
                elif rel.target_id == current:
                    neighbor = rel.source_id
                else:
                    continue

                traversed_rels[rel.id] = rel
                entity = self._entities.get(neighbor)
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
