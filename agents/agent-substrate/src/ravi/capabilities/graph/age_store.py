"""Apache AGE graph store — PostgreSQL extension for openCypher queries.

Uses ``asyncpg`` raw SQL with AGE extension functions.  Requires the
``age`` extension to be installed in PostgreSQL.

Usage::

    from ravi.capabilities.graph.age_store import AGEGraphStore

    store = AGEGraphStore(dsn="postgresql://...", graph_name="knowledge")
    await store.connect()
    await store.add_entities([Entity(label="Person", properties={"name": "Alice"})])
    results = await store.query_cypher("MATCH (n:Person) RETURN n")
"""

from __future__ import annotations
from ravi.logger import setup_logging

import json
from typing import Any, Optional

import asyncpg

from ravi.kernel.graph import Entity, Relationship, SubGraph

logger = setup_logging()


def _escape_props(props: dict[str, Any]) -> str:
    """Escape a properties dict for Cypher."""
    parts: list[str] = []
    for k, v in props.items():
        if isinstance(v, str):
            # Escape single quotes in strings
            escaped = v.replace("\\", "\\\\").replace("'", "\\'")
            parts.append(f"{k}: '{escaped}'")
        elif isinstance(v, bool):
            parts.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            parts.append(f"{k}: {v}")
        else:
            escaped = json.dumps(v).replace("'", "\\'")
            parts.append(f"{k}: '{escaped}'")
    return "{" + ", ".join(parts) + "}" if parts else "{}"


class AGEGraphStore:
    """Apache AGE (PostgreSQL graph extension) store.

    This store uses raw asyncpg connections to execute Cypher queries
    via the AGE ``cypher()`` function.  The graph is created automatically
    on first use.

    Args:
        dsn: PostgreSQL DSN (e.g. ``"postgresql://postgres:postgres@localhost/agentdb"``).
        graph_name: Name of the graph to use/create.
    """

    def __init__(
        self,
        dsn: str,
        graph_name: str = "knowledge",
    ) -> None:
        self._dsn = dsn
        self._graph_name = graph_name
        self._pool: Optional[asyncpg.Pool] = None
        self._initialized = False

    async def connect(self) -> None:
        """Create the connection pool and initialize AGE."""
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        await self._ensure_graph()

    async def disconnect(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _ensure_graph(self) -> None:
        """Create the AGE extension and graph if they don't exist."""
        if self._initialized or not self._pool:
            return

        async with self._pool.acquire() as conn:
            # Load AGE extension
            await conn.execute("CREATE EXTENSION IF NOT EXISTS age")
            await conn.execute("LOAD 'age'")
            await conn.execute("SET search_path = ag_catalog, '$user', public")

            # Create graph if not exists
            exists = await conn.fetchval(
                "SELECT count(*) FROM ag_catalog.ag_graph WHERE name = $1",
                self._graph_name,
            )
            if not exists:
                await conn.execute(f"SELECT create_graph('{self._graph_name}')")
                logger.info("Created graph '%s'", self._graph_name)

        self._initialized = True

    async def _execute_cypher(
        self,
        cypher: str,
        columns: str = "v agtype",
    ) -> list[Any]:
        """Execute a Cypher query via AGE and return raw results."""
        if not self._pool:
            raise RuntimeError("Not connected — call connect() first")

        sql = f"""
            SELECT * FROM cypher('{self._graph_name}', $$
                {cypher}
            $$) as ({columns})
        """

        async with self._pool.acquire() as conn:
            await conn.execute("LOAD 'age'")
            await conn.execute("SET search_path = ag_catalog, '$user', public")
            rows = await conn.fetch(sql)
            return [dict(row) for row in rows]

    async def add_entities(self, entities: list[Entity]) -> list[str]:
        ids: list[str] = []
        for entity in entities:
            props = {**entity.properties, "_id": entity.id}
            cypher = f"CREATE (n:{entity.label} {_escape_props(props)}) RETURN n"
            await self._execute_cypher(cypher)
            ids.append(entity.id)
        return ids

    async def add_relationships(self, relationships: list[Relationship]) -> list[str]:
        ids: list[str] = []
        for rel in relationships:
            props = {**rel.properties, "_id": rel.id}
            cypher = (
                f"MATCH (a {{_id: '{rel.source_id}'}}), (b {{_id: '{rel.target_id}'}}) "
                f"CREATE (a)-[r:{rel.type} {_escape_props(props)}]->(b) "
                f"RETURN r"
            )
            await self._execute_cypher(cypher)
            ids.append(rel.id)
        return ids

    async def query_cypher(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        # AGE doesn't support parameterized Cypher natively,
        # so we do simple string interpolation for now.
        # NOTE: For production use, consider input validation.
        effective_query = query
        if params:
            for k, v in params.items():
                placeholder = f"${k}"
                if isinstance(v, str):
                    escaped = v.replace("'", "\\'")
                    effective_query = effective_query.replace(
                        placeholder, f"'{escaped}'"
                    )
                else:
                    effective_query = effective_query.replace(placeholder, str(v))

        return await self._execute_cypher(effective_query)

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: Optional[list[str]] = None,
    ) -> SubGraph:
        rel_filter = ""
        if relationship_types:
            rel_filter = ":" + "|".join(relationship_types)

        cypher = (
            f"MATCH (a {{_id: '{entity_id}'}})-[r{rel_filter}*1..{depth}]-(b) "
            f"RETURN a, r, b"
        )

        try:
            rows = await self._execute_cypher(
                cypher, columns="a agtype, r agtype, b agtype"
            )
        except Exception:
            logger.warning(
                "Neighbor query failed for entity %s", entity_id, exc_info=True
            )
            return SubGraph()

        # Parse results into entities and relationships
        entities: dict[str, Entity] = {}
        relationships: list[Relationship] = []

        for row in rows:
            # Results from AGE are agtype JSON strings — parse them
            for key in ("a", "b"):
                raw = row.get(key)
                if raw and isinstance(raw, str):
                    try:
                        data = json.loads(raw)
                        eid = data.get("_id", str(data.get("id", "")))
                        label = data.get("label", "Unknown")
                        props = {
                            k: v
                            for k, v in data.items()
                            if k not in ("id", "_id", "label")
                        }
                        entities[eid] = Entity(id=eid, label=label, properties=props)
                    except (json.JSONDecodeError, TypeError):
                        pass

        return SubGraph(
            entities=tuple(entities.values()),
            relationships=tuple(relationships),
        )

    async def delete_entity(self, entity_id: str) -> bool:
        cypher = f"MATCH (n {{_id: '{entity_id}'}}) DETACH DELETE n RETURN count(n)"
        try:
            result = await self._execute_cypher(cypher)
            return bool(result)
        except Exception:
            logger.warning("Delete entity failed for %s", entity_id, exc_info=True)
            return False

    async def delete_relationship(self, relationship_id: str) -> bool:
        cypher = f"MATCH ()-[r {{_id: '{relationship_id}'}}]-() DELETE r RETURN count(r)"
        try:
            result = await self._execute_cypher(cypher)
            return bool(result)
        except Exception:
            logger.warning("Delete relationship failed for %s", relationship_id, exc_info=True)
            return False
