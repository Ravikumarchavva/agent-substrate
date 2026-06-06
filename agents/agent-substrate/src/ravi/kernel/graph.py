"""Graph store contracts — Protocol and shared value types for knowledge graphs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class Entity:
    """A node in the knowledge graph."""

    label: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Relationship:
    """An edge between two entities in the knowledge graph."""

    source_id: str
    target_id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SubGraph:
    """A subgraph result containing entities and relationships."""

    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)


class GraphStore(Protocol):
    """Contract every graph store adapter must satisfy."""

    async def add_entities(self, entities: list[Entity]) -> list[str]: ...

    async def add_relationships(self, relationships: list[Relationship]) -> list[str]: ...

    async def query_cypher(
        self, query: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]: ...

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: Optional[list[str]] = None,
    ) -> SubGraph: ...

    async def delete_entity(self, entity_id: str) -> bool: ...


__all__ = ["Entity", "Relationship", "SubGraph", "GraphStore"]
