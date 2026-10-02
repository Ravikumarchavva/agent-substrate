"""LocalFilesystemMemoryStore — JSON-file-backed durable memory records.

Stores data in a local directory tree (default: ``./data/db/memory``), the
same "create a folder on first use" convention as every other ``Local*``
store in this package. The canonical zero-infra ``MemoryStore``
(``kernel/storage/memory.py``) default — ``integrations/memory/durable_memory_store.py``
(Postgres) is the L2 production upgrade for the same Protocol.

Layout::

    <root>/
      records/<tenant_id>/<record_id>.json   — one file per MemoryRecord

Partitioned by ``tenant_id``, and a record is addressed by ``(tenant, id)``: a bare id
never finds anything, so one tenant cannot reach another's record by guessing or reusing
an id. ``query()`` loads one tenant's records and filters/scores them (visibility,
``categories``/``statuses``/``metadata_filter``) in memory, same "brute force is correct at
local scale" reasoning as ``local_vector.py``'s ``search()``.

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

from substrate.kernel.abstractions.exceptions import ScopeViolationError
from substrate.kernel.abstractions.storage.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)
from substrate.kernel.storage.fs import atomic_write_json, safe_name


class LocalFilesystemMemoryStore:
    """Filesystem-backed MemoryStore — stores records as JSON files."""

    def __init__(self, root: str | Path = "./data/db/memory") -> None:
        self._root = Path(root)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _tenant_dir(self, tenant_id: str) -> Path:
        return self._root / "records" / safe_name(tenant_id)

    def _record_path(self, tenant_id: str, record_id: str) -> Path:
        return self._tenant_dir(tenant_id) / f"{safe_name(record_id)}.json"

    def _load(self, path: Path) -> MemoryRecord:
        return MemoryRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _visible(self, caller: MemoryNamespace, record_id: str) -> tuple[Path, MemoryRecord] | None:
        """The record at ``(caller's tenant, id)``, if the caller may see it."""
        path = self._record_path(caller.tenant_id, record_id)
        if not path.exists():
            return None
        record = self._load(path)
        return (path, record) if record.namespace.visible_from(caller) else None

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
            except (OSError, ValueError):
                pass
        return records

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
        path = self._record_path(record.namespace.tenant_id, record.id)
        if path.exists() and self._load(path).namespace != record.namespace:
            raise ScopeViolationError(
                f"record {record.id!r} already belongs to a different namespace in tenant {record.namespace.tenant_id!r}",
                record_id=record.id,
            )
        self._save(record)
        return record.id

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        found = self._visible(caller, record_id)
        return found[1] if found else None

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        found = self._visible(caller, record_id)
        if found is None or not found[1].namespace.owned_by(caller):
            return False
        found[0].unlink()
        return True

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        caller = spec.namespace
        candidates = [
            (r, mtime)
            for r, mtime in self._load_tenant_records(caller.tenant_id)
            if (spec.tenant_wide is not None or r.namespace.visible_from(caller))
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

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        now = datetime.now(tz=timezone.utc)
        for record_id in record_ids:
            found = self._visible(caller, record_id)
            if found is None:
                continue
            record = found[1]
            self._save(
                record.model_copy(update={"last_accessed_at": now, "access_count": record.access_count + 1})
            )

    async def erase(self, within: MemoryNamespace) -> int:
        erased = 0
        for record, _mtime in self._load_tenant_records(within.tenant_id):
            if record.namespace.within(within):
                self._record_path(record.namespace.tenant_id, record.id).unlink(missing_ok=True)
                erased += 1
        return erased


__all__ = ["LocalFilesystemMemoryStore"]
