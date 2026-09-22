"""LocalFilesystemGraphStore — JSON-file-backed knowledge graph store.

Stores data in a local directory tree (default: ``./data/db/graph``), mirroring
``LocalFilesystemHistoryProvider``'s convention of creating a folder on first
use instead of requiring an external database.

Layout::

    <root>/
      entities/<entity_id>.json          — one file per Entity (+ "namespace" field)
      relationships/<relationship_id>.json — one file per Relationship (+ "namespace" field)

``get_neighbors`` loads every entity/relationship file and runs the same
breadth-first traversal as :class:`InMemoryGraphStore` — this is a durability
upgrade, not a redesign of the traversal algorithm.

This store is intentionally a drop-in replacement for ``InMemoryGraphStore``
for local dev / experimentation — it does NOT require Postgres/AGE.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from pathlib import Path

from substrate.kernel.storage.graph import Entity, Relationship, SubGraph


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON to a file atomically (tmp → rename) to avoid corruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class LocalFilesystemGraphStore:
    """Filesystem-backed GraphStore — stores entities/relationships as JSON files.

    Suitable for local development and experimentation without requiring a
    running Postgres/AGE instance. Every entity/relationship remembers the
    namespace it was written under: ``namespace=""`` sees the whole graph, a
    non-empty namespace sees only what was written under it — same contract
    as :class:`InMemoryGraphStore`.

    Args:
        root: Path to the storage root directory. Created automatically on
            first write. Defaults to ``./data/db/graph``.
    """

    def __init__(self, root: str | Path = "./data/db/graph") -> None:
        self._root = Path(root)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _entity_path(self, entity_id: str) -> Path:
        return self._root / "entities" / f"{entity_id}.json"

    def _relationship_path(self, relationship_id: str) -> Path:
        return self._root / "relationships" / f"{relationship_id}.json"

    def _save_entity(self, entity: Entity, namespace: str) -> None:
        data = entity.model_dump(mode="json")
        data["namespace"] = namespace
        _atomic_write(self._entity_path(entity.id), data)

    def _save_relationship(self, rel: Relationship, namespace: str) -> None:
        data = rel.model_dump(mode="json")
        data["namespace"] = namespace
        _atomic_write(self._relationship_path(rel.id), data)

    def _load_all_entities(self) -> dict[str, tuple[Entity, str]]:
        entities_dir = self._root / "entities"
        if not entities_dir.exists():
            return {}
        result: dict[str, tuple[Entity, str]] = {}
        for p in entities_dir.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                namespace = raw.pop("namespace", "")
                entity = Entity.model_validate(raw)
                result[entity.id] = (entity, namespace)
            except Exception:
                pass
        return result

    def _load_all_relationships(self) -> dict[str, tuple[Relationship, str]]:
        rels_dir = self._root / "relationships"
        if not rels_dir.exists():
            return {}
        result: dict[str, tuple[Relationship, str]] = {}
        for p in rels_dir.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                namespace = raw.pop("namespace", "")
                rel = Relationship.model_validate(raw)
                result[rel.id] = (rel, namespace)
            except Exception:
                pass
        return result

    @staticmethod
    def _visible(namespace_of_item: str, namespace: str) -> bool:
        return not namespace or namespace_of_item == namespace

    # ── Write ─────────────────────────────────────────────────────────────

    async def add_entities(
        self, entities: list[Entity], *, namespace: str = ""
    ) -> list[str]:
        for entity in entities:
            self._save_entity(entity, namespace)
        return [e.id for e in entities]

    async def add_relationships(
        self, relationships: list[Relationship], *, namespace: str = ""
    ) -> list[str]:
        for rel in relationships:
            self._save_relationship(rel, namespace)
        return [r.id for r in relationships]

    async def delete_entity(self, entity_id: str, *, namespace: str = "") -> bool:
        entities = self._load_all_entities()
        if entity_id not in entities:
            return False
        _, item_namespace = entities[entity_id]
        if not self._visible(item_namespace, namespace):
            return False
        self._entity_path(entity_id).unlink(missing_ok=True)
        # DETACH DELETE: drop every visible relationship touching this entity.
        for rid, (rel, rel_namespace) in self._load_all_relationships().items():
            if entity_id in (rel.source_id, rel.target_id) and self._visible(
                rel_namespace, namespace
            ):
                self._relationship_path(rid).unlink(missing_ok=True)
        return True

    async def delete_relationship(
        self, relationship_id: str, *, namespace: str = ""
    ) -> bool:
        relationships = self._load_all_relationships()
        if relationship_id not in relationships:
            return False
        _, item_namespace = relationships[relationship_id]
        if not self._visible(item_namespace, namespace):
            return False
        self._relationship_path(relationship_id).unlink(missing_ok=True)
        return True

    # ── Read ──────────────────────────────────────────────────────────────

    async def get_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        relationship_types: list[str] | None = None,
        namespace: str = "",
    ) -> SubGraph:
        entities = {
            i: e
            for i, (e, ns) in self._load_all_entities().items()
            if self._visible(ns, namespace)
        }
        relationships = {
            i: r
            for i, (r, ns) in self._load_all_relationships().items()
            if self._visible(ns, namespace)
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


__all__ = ["LocalFilesystemGraphStore"]
