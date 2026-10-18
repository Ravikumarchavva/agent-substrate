"""Graph store contracts — Protocol and shared value types for knowledge graphs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from substrate.types.content import JsonObject, KernelModel
from substrate.types.ids import new_id


class Entity(KernelModel):
    """A node in the knowledge graph."""

    label: str
    properties: JsonObject = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: new_id())


class Relationship(KernelModel):
    """An edge between two entities in the knowledge graph."""

    source_id: str
    target_id: str
    type: str
    properties: JsonObject = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: new_id())


class SubGraph(KernelModel):
    """A subgraph result containing entities and relationships."""

    entities: tuple[Entity, ...] = Field(default_factory=tuple)
    relationships: tuple[Relationship, ...] = Field(default_factory=tuple)


@runtime_checkable
class GraphStore(Protocol):
    """Contract every graph store adapter must satisfy.

    The core protocol is intentionally query-language-agnostic.
    Stores that support Cypher implement the ``CypherCapable`` protocol
    below as an additional capability — callers can check with
    ``isinstance(store, CypherCapable)`` before issuing Cypher queries.

    ``namespace`` scopes every operation to one tenant/agent's slice of the
    graph. ``""`` (the default) means unscoped — the whole graph, exactly the
    pre-namespace behaviour. A non-empty namespace tags what it writes and
    only sees/deletes what carries the same tag, so two tenants using the same
    store cannot read or delete each other's entities.
    """

    async def add_entities(
        self, entities: list[Entity], *, namespace: str = ""
    ) -> list[str]: ...

    async def add_relationships(
        self, relationships: list[Relationship], *, namespace: str = ""
    ) -> list[str]: ...

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
        namespace: str = "",
    ) -> SubGraph: ...

    async def delete_entity(self, entity_id: str, *, namespace: str = "") -> bool: ...

    async def delete_relationship(
        self, relationship_id: str, *, namespace: str = ""
    ) -> bool: ...


@runtime_checkable
class CypherCapable(Protocol):
    """Optional capability for graph stores that speak Cypher (e.g. Neo4j, AGE).

    Check with ``isinstance(store, CypherCapable)`` before calling
    ``query_cypher`` — stores like Gremlin or SPARQL backends will not
    implement this.
    """

    async def query_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class EntityFinder(Protocol):
    """Optional capability: find entities by the words in their name (or, with no name, their id).

    Retrieval over a knowledge graph starts from the entities a question mentions. A store that can answer that
    directly implements this; one that only speaks Cypher is asked with ``CypherCapable`` instead.
    """

    async def find_entities(
        self, terms: Sequence[str], *, limit: int = 100, namespace: str = ""
    ) -> list[Entity]: ...


__all__ = [
    "Entity",
    "Relationship",
    "SubGraph",
    "GraphStore",
    "CypherCapable",
    "EntityFinder",
]
