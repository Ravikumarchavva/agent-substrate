"""LocalFilesystemMemoryStore — JSON-file-backed durable memory records.

Stores data in a local directory tree (default: ``./data/db/memory``), the
same "create a folder on first use" convention as every other ``Local*``
store in this package. The canonical zero-infra ``MemoryStore``
(``kernel/storage/memory.py``) default — ``integrations/memory/durable_memory_store.py``
(Postgres) is the L2 production upgrade for the same Protocol.

Layout::

    <root>/
      records/<tenant_id>/<record_id>.json   — one file per MemoryRecord

Partitioned by ``tenant_id`` only (the one namespace field every record
always has) — ``query()`` loads a tenant's records and filters/scores the
rest (``user_id``/``agent_id``/``session_id``/``categories``/``statuses``/
``metadata_filter``) in memory, same "brute force is correct at local
scale" reasoning as ``local_vector.py``'s ``search()``.

Scoring matches ``integrations/memory/durable_memory_store.py`` (the
Postgres reference implementation) exactly: substring/keyword match against
``spec.text_query`` when given (its full-text-search equivalent), else a
uniform score ordered by recency. ``MemoryRecord`` carries no ``embedding``
field — ``spec.embedding``-based ranking isn't implemented by the reference
store either, so this store doesn't invent a different contract.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from urllib.parse import unquote

from substrate.agents.storage.fs import atomic_write_json, safe_name
from substrate.kernel.storage.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)


class LocalFilesystemMemoryStore:
    """Filesystem-backed MemoryStore — stores records as JSON files."""

    def __init__(self, root: str | Path = "./data/db/memory") -> None:
        self._root = Path(root)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _tenant_dir(self, tenant_id: str) -> Path:
        return self._root / "records" / safe_name(tenant_id)

    def _record_path(self, tenant_id: str, record_id: str) -> Path:
        return self._tenant_dir(tenant_id) / f"{safe_name(record_id)}.json"

    def _find_record(self, record_id: str) -> tuple[str, Path] | None:
        """Locate a record by id without knowing its tenant up front —
        record ids are globally unique (uuid4), so a scan across tenant
        dirs is correct; at local scale this is fine (same reasoning as
        every other brute-force local store)."""
        records_dir = self._root / "records"
        if not records_dir.exists():
            return None
        for tenant_dir in records_dir.iterdir():
            if not tenant_dir.is_dir():
                continue
            p = tenant_dir / f"{safe_name(record_id)}.json"
            if p.exists():
                return unquote(tenant_dir.name), p
        return None

    def _load(self, path: Path) -> MemoryRecord:
        return MemoryRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _save(self, record: MemoryRecord) -> None:
        path = self._record_path(record.namespace.tenant_id, record.id)
        atomic_write_json(path, json.loads(record.model_dump_json()))

    def _load_tenant_records(self, tenant_id: str) -> list[tuple[MemoryRecord, float]]:
        """Return ``(record, mtime)`` pairs — file mtime stands in for
        creation order, since ``MemoryRecord`` carries no timestamp field
        (confirmed against the Postgres reference store: ``created_at`` is
        a DB-only column, never part of the model itself)."""
        tenant_dir = self._tenant_dir(tenant_id)
        if not tenant_dir.exists():
            return []
        records: list[tuple[MemoryRecord, float]] = []
        for p in tenant_dir.glob("*.json"):
            try:
                records.append((self._load(p), p.stat().st_mtime))
            except Exception:
                pass
        return records

    @staticmethod
    def _matches_namespace(record: MemoryRecord, ns: MemoryNamespace) -> bool:
        if record.namespace.tenant_id != ns.tenant_id:
            return False
        if ns.user_id is not None and record.namespace.user_id != ns.user_id:
            return False
        if ns.agent_id is not None and record.namespace.agent_id != ns.agent_id:
            return False
        if ns.session_id is not None and record.namespace.session_id != ns.session_id:
            return False
        return True

    @staticmethod
    def _matches_metadata(record: MemoryRecord, filter: dict | None) -> bool:
        if not filter:
            return True
        return all(record.metadata.get(k) == v for k, v in filter.items())

    @staticmethod
    def _record_text(record: MemoryRecord) -> str:
        parts = []
        for block in record.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return " ".join(parts)

    # ── MemoryStore Protocol ─────────────────────────────────────────────

    async def save(self, record: MemoryRecord) -> str:
        self._save(record)
        return record.id

    async def get(self, record_id: str) -> MemoryRecord | None:
        found = self._find_record(record_id)
        if found is None:
            return None
        _, path = found
        return self._load(path)

    async def delete(self, record_id: str) -> bool:
        found = self._find_record(record_id)
        if found is None:
            return False
        _, path = found
        path.unlink()
        return True

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        candidates = [
            (r, mtime)
            for r, mtime in self._load_tenant_records(spec.namespace.tenant_id)
            if self._matches_namespace(r, spec.namespace)
            and r.status in spec.statuses
            and (spec.categories is None or r.category in spec.categories)
            and self._matches_metadata(r, spec.metadata_filter)
        ]

        if spec.text_query:
            needle = spec.text_query.lower()
            method = "fulltext"
            scored = [
                (1.0, mtime, record)
                for record, mtime in candidates
                if needle in self._record_text(record).lower()
            ]
        else:
            # No text_query: uniform score, most-recent first — matches
            # DurableMemoryStore's `ORDER BY created_at DESC` fallback.
            method = "default"
            scored = [(1.0, mtime, record) for record, mtime in candidates]

        scored.sort(key=lambda t: t[1], reverse=True)
        scored = [t for t in scored if t[0] >= spec.min_score]
        return [
            MemoryMatch(record=record, score=score, rank=i, retrieval_method=method)
            for i, (score, _mtime, record) in enumerate(scored[: spec.limit])
        ]

    async def touch(self, record_ids: Sequence[str]) -> None:
        now = datetime.now(tz=timezone.utc)
        for record_id in record_ids:
            found = self._find_record(record_id)
            if found is None:
                continue
            _, path = found
            record = self._load(path)
            updated = record.model_copy(
                update={
                    "last_accessed_at": now,
                    "access_count": record.access_count + 1,
                }
            )
            self._save(updated)

    async def clear(self, namespace: MemoryNamespace) -> None:
        for record, _mtime in self._load_tenant_records(namespace.tenant_id):
            if self._matches_namespace(record, namespace):
                path = self._record_path(record.namespace.tenant_id, record.id)
                path.unlink(missing_ok=True)


__all__ = ["LocalFilesystemMemoryStore"]
