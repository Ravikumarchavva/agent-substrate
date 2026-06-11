"""Graph store contracts — Protocol and shared value types for knowledge graphs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable



@dataclass(frozen=True)
class Entity:
    """A node in the knowledge graph."""

    label: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class Relationship:
    """An edge between two entities in the knowledge graph."""

    source_id: str
    target_id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class SubGraph:
    """A subgraph result containing entities and relationships."""

    entities: tuple[Entity, ...] = field(default_factory=tuple)
    relationships: tuple[Relationship, ...] = field(default_factory=tuple)


@runtime_checkable
class GraphStore(Protocol):

    """Contract every graph store adapter must satisfy.

    The core protocol is intentionally query-language-agnostic.
    Stores that support Cypher implement the ``CypherCapable`` protocol
    below as an additional capability — callers can check with
    ``isinstance(store, CypherCapable)`` before issuing Cypher queries.
    """

    async def add_entities(self, entities: list[Entity]) -> list[str]: ...

    async def add_relationships(
        self, relationships: list[Relationship]
    ) -> list[str]: ...

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: Optional[list[str]] = None,
    ) -> SubGraph: ...

    async def delete_entity(self, entity_id: str) -> bool: ...

    async def delete_relationship(self, relationship_id: str) -> bool: ...


@runtime_checkable
class CypherCapable(Protocol):

    """Optional capability for graph stores that speak Cypher (e.g. Neo4j, AGE).

    Check with ``isinstance(store, CypherCapable)`` before calling
    ``query_cypher`` — stores like Gremlin or SPARQL backends will not
    implement this.
    """

    async def query_cypher(
        self, query: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]: ...


__all__ = ["Entity", "Relationship", "SubGraph", "GraphStore", "CypherCapable"]
