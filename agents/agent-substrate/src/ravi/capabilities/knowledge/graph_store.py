"""Abstract graph store and shared data types for knowledge graphs.

Concrete implementation (``AGEGraphStore``) lives in ``integrations/graph/``.
This module stays in ``core/`` with zero external dependencies.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Entity:
    """A node in the knowledge graph.

    Attributes:
        label: The entity type (e.g. ``"Person"``, ``"Company"``).
        properties: Key-value properties of the entity.
        id: Unique identifier.
    """

    label: str
    properties: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Relationship:
    """An edge between two entities in the knowledge graph.

    Attributes:
        source_id: ID of the source entity.
        target_id: ID of the target entity.
        type: Relationship type (e.g. ``"WORKS_AT"``, ``"KNOWS"``).
        properties: Key-value properties of the relationship.
        id: Unique identifier.
    """

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


class BaseGraphStore(ABC):
    """Abstract base class for graph stores (knowledge graphs)."""

    @abstractmethod
    async def add_entities(self, entities: list[Entity]) -> list[str]:
        """Add entities (nodes) to the graph.

        Returns:
            List of entity IDs that were stored.
        """
        ...

    @abstractmethod
    async def add_relationships(self, relationships: list[Relationship]) -> list[str]:
        """Add relationships (edges) to the graph.

        Returns:
            List of relationship IDs that were stored.
        """
        ...

    @abstractmethod
    async def query_cypher(
        self, query: str, params: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query and return results.

        Args:
            query: openCypher query string.
            params: Optional query parameters.

        Returns:
            List of result rows as dictionaries.
        """
        ...

    @abstractmethod
    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: Optional[list[str]] = None,
    ) -> SubGraph:
        """Get the neighborhood of an entity.

        Args:
            entity_id: The entity to find neighbors for.
            depth: Traversal depth (1 = direct neighbors).
            relationship_types: Filter by relationship types.

        Returns:
            A ``SubGraph`` containing the entity and its neighbors.
        """
        ...

    @abstractmethod
    async def delete_entity(self, entity_id: str) -> bool:
        """Delete an entity and its relationships."""
        ...
