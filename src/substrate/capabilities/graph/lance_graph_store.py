"""LanceGraphStore — Lance-backed GraphStore, NetworkX for traversal.

Sibling to ``AGEGraphStore`` (``age_store.py`` — Postgres/Apache AGE, the
shared tenant-level graph) — same ``GraphStore`` Protocol
(``kernel/storage/graph.py``), different backend, aimed at the *per-user*
session-document knowledge graph (100-500 entities/edges per user is the
real scale here — see the storage plan) rather than a standing multi-tenant
graph.

Kùzu (an embedded graph DB with native Cypher) was considered and rejected
for this: archived October 2025 after its Apple acquisition, so it's not a
dependency worth taking on now. At this scale — a few hundred nodes/edges —
a real graph *database* isn't needed at all: entities/relationships are
stored as plain Lance rows (adjacency-list shape, the same pattern
Microsoft's GraphRAG project uses for its own LanceDB-backed entity/
relationship tables), and the one thing that actually needs graph
*algorithms* — ``get_neighbors``'s multi-hop traversal — loads the small
row set into an in-memory NetworkX graph and runs a real BFS. NetworkX is
mature, pure Python (no compiled-extension risk), and at this row count
building the graph fresh per call is sub-millisecond.

``query_cypher`` (the ``CypherCapable`` capability) is deliberately narrow,
not a general Cypher interpreter: ``GraphRAGPipeline.query()``
(``capabilities/knowledge/graph_rag.py``) is the only caller in this
codebase, and it only ever issues one exact query shape —
``"MATCH (n) RETURN n LIMIT <N>"`` — to fetch every entity for its own
Python-side keyword matching (there is no non-Cypher fallback path for
that in ``GraphRAGPipeline`` today, confirmed by reading it; without this,
plugging in a non-``CypherCapable`` store would make that pipeline's graph
enrichment silently return nothing, forever). Implementing exactly that one
pattern — nothing more — unblocks the existing pipeline unmodified rather
than requiring a change to code that already works. Any other Cypher string
raises ``NotImplementedError`` with a clear message, rather than silently
returning wrong or empty results.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from substrate.kernel.storage.graph import Entity, Relationship, SubGraph

_CYPHER_MATCH_ALL = re.compile(
    r"^\s*MATCH\s*\(\s*n\s*\)\s*RETURN\s+n(?:\s+LIMIT\s+(\d+))?\s*;?\s*$", re.IGNORECASE
)


def _entities_schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("id", pa.utf8()),
            pa.field("label", pa.utf8()),
            pa.field("properties_json", pa.utf8()),
            pa.field("session_id", pa.utf8()),
        ]
    )


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


def _scoped_id_filter(row_id: str, scope: str | None) -> str:
    clause = f"id = '{_sql_escape(row_id)}'"
    return f"{clause} AND {scope}" if scope else clause


def _relationships_schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("id", pa.utf8()),
            pa.field("source_id", pa.utf8()),
            pa.field("target_id", pa.utf8()),
            pa.field("type", pa.utf8()),
            pa.field("properties_json", pa.utf8()),
            pa.field("session_id", pa.utf8()),
        ]
    )


class LanceGraphStore:
    """``GraphStore`` (+ narrow ``CypherCapable``) backed by two Lance
    tables (``entities``, ``relationships``), local file or remote Lance
    Namespace catalog — same dual connection mode as
    ``LanceDBVectorStore``/``LanceLongTermMemory``.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        namespace_uri: str | None = None,
        namespace_path: list[str] | None = None,
        storage_options: dict[str, str] | None = None,
        session_id: str = "",
    ) -> None:
        if bool(path) == bool(namespace_uri):
            raise ValueError(
                "LanceGraphStore needs exactly one of `path` or `namespace_uri`"
            )
        if namespace_uri and not namespace_path:
            raise ValueError("namespace_path is required with namespace_uri")
        self._path = str(path) if path else None
        self._namespace_uri = namespace_uri
        self._namespace_path = namespace_path
        self._storage_options = storage_options
        # Tags every row this instance writes — callers wanting a
        # session-scoped view construct one instance per session_id; a
        # user-wide view (no filter) is a plain instance with session_id="".
        self._session_id = session_id
        self._db = None
        self._namespace_ready = False

    async def _connection(self):
        if self._db is None:
            import lancedb

            if self._namespace_uri:
                self._db = lancedb.connect_namespace_async(
                    "rest",
                    {"uri": self._namespace_uri},
                    storage_options=self._storage_options,
                )
            else:
                self._db = await lancedb.connect_async(self._path)
        if self._namespace_uri and not self._namespace_ready:
            await self._db.create_namespace(self._namespace_path, mode="exist_ok")
            self._namespace_ready = True
        return self._db

    def _table_kwargs(self) -> dict[str, Any]:
        if not self._namespace_uri:
            return {}
        kwargs: dict[str, Any] = {"namespace_path": self._namespace_path}
        if self._storage_options:
            kwargs["storage_options"] = self._storage_options
        return kwargs

    async def _table_names(self, db) -> list[str]:
        names: list[str] = []
        page_token = None
        ns_kwargs = {"namespace_path": self._namespace_path} if self._namespace_uri else {}
        while True:
            resp = await db.list_tables(page_token=page_token, **ns_kwargs)
            names.extend(resp.tables)
            if not resp.page_token:
                break
            page_token = resp.page_token
        return names

    async def _table(self, name: str, schema):
        db = await self._connection()
        if name in await self._table_names(db):
            return await db.open_table(name, **self._table_kwargs())
        return await db.create_table(name, schema=schema(), **self._table_kwargs())

    async def _entities_table(self):
        return await self._table("entities", _entities_schema)

    async def _relationships_table(self):
        return await self._table("relationships", _relationships_schema)

    # ── GraphStore Protocol ──────────────────────────────────────────────

    def _scope_filter(self, namespace: str) -> str | None:
        """SQL filter for a per-call namespace; ``None`` = unscoped."""
        return f"session_id = '{_sql_escape(namespace)}'" if namespace else None

    async def add_entities(
        self, entities: list[Entity], *, namespace: str = ""
    ) -> list[str]:
        if not entities:
            return []
        table = await self._entities_table()
        await table.add(
            [
                {
                    "id": e.id,
                    "label": e.label,
                    "properties_json": json.dumps(e.properties),
                    "session_id": namespace or self._session_id,
                }
                for e in entities
            ]
        )
        return [e.id for e in entities]

    async def add_relationships(
        self, relationships: list[Relationship], *, namespace: str = ""
    ) -> list[str]:
        if not relationships:
            return []
        table = await self._relationships_table()
        rows = []
        for r in relationships:
            rows.append(
                {
                    "id": r.id,
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "type": r.type,
                    "properties_json": json.dumps(r.properties),
                    "session_id": namespace or self._session_id,
                }
            )
        await table.add(rows)
        return [row["id"] for row in rows]

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
        namespace: str = "",
    ) -> SubGraph:
        import networkx as nx

        ent_table = await self._entities_table()
        rel_table = await self._relationships_table()
        scope = self._scope_filter(namespace)
        ent_query, rel_query = ent_table.query(), rel_table.query()
        if scope:
            ent_query, rel_query = ent_query.where(scope), rel_query.where(scope)
        entity_rows = await ent_query.to_list()
        rel_rows = await rel_query.to_list()
        if relationship_types:
            rel_rows = [r for r in rel_rows if r["type"] in relationship_types]

        entities_by_id = {row["id"]: row for row in entity_rows}
        graph = nx.Graph()  # undirected — matches AGEGraphStore's `-[r]-` pattern
        graph.add_nodes_from(entities_by_id)
        for row in rel_rows:
            graph.add_edge(row["source_id"], row["target_id"], **row)

        if entity_id not in graph:
            return SubGraph()

        # BFS up to `depth` hops, excluding the start node itself — matches
        # Cypher's `(a)-[r*1..depth]-(b)` semantics (b != a).
        reachable = nx.single_source_shortest_path_length(graph, entity_id, cutoff=depth)
        neighbor_ids = {nid for nid in reachable if nid != entity_id}

        found_entities = [
            Entity(
                id=nid,
                label=entities_by_id[nid]["label"],
                properties=json.loads(entities_by_id[nid]["properties_json"]),
            )
            for nid in neighbor_ids
            if nid in entities_by_id
        ]
        found_relationships = [
            Relationship(
                source_id=row["source_id"],
                target_id=row["target_id"],
                type=row["type"],
                properties=json.loads(row["properties_json"]),
                id=row["id"],
            )
            for row in rel_rows
            if row["source_id"] in ({entity_id} | neighbor_ids)
            and row["target_id"] in ({entity_id} | neighbor_ids)
        ]
        return SubGraph(
            entities=tuple(found_entities), relationships=tuple(found_relationships)
        )

    async def delete_entity(self, entity_id: str, *, namespace: str = "") -> bool:
        table = await self._entities_table()
        before = await table.count_rows()
        await table.delete(_scoped_id_filter(entity_id, self._scope_filter(namespace)))
        after = await table.count_rows()
        return after < before

    async def delete_relationship(
        self, relationship_id: str, *, namespace: str = ""
    ) -> bool:
        table = await self._relationships_table()
        before = await table.count_rows()
        await table.delete(
            _scoped_id_filter(relationship_id, self._scope_filter(namespace))
        )
        after = await table.count_rows()
        return after < before

    # ── CypherCapable — narrow, see module docstring ────────────────────

    async def query_cypher(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        match = _CYPHER_MATCH_ALL.match(query)
        if not match:
            raise NotImplementedError(
                "LanceGraphStore.query_cypher only supports the exact pattern "
                "'MATCH (n) RETURN n [LIMIT <n>]' — the one shape "
                "GraphRAGPipeline.query() issues. Got: " + query
            )
        limit = int(match.group(1)) if match.group(1) else None
        table = await self._entities_table()
        rows = await table.query().to_list()
        if limit is not None:
            rows = rows[:limit]
        return [
            {
                "n": json.dumps(
                    {
                        "_id": row["id"],
                        "label": row["label"],
                        **json.loads(row["properties_json"]),
                    }
                )
            }
            for row in rows
        ]


__all__ = ["LanceGraphStore"]
