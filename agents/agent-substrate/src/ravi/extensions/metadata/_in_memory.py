"""In-process reference implementation of :class:`MetadataStore`.

The store keeps separate hot and cold dictionaries per tenant so callers can
exercise tier transitions without any external Redis/Postgres/S3 services.
Production backends should preserve the same tenant scoping and timestamp
semantics while swapping these dictionaries for real substrates.

Thread-safety
~~~~~~~~~~~~~
All shared metadata maps are guarded by one ``threading.RLock``. Async methods
do not await while the lock is held, making the implementation safe for both
asyncio tasks and background threads under free-threaded Python.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ravi.kernel.metadata import (
    KeyNotFoundError,
    MetadataRecord,
    Tier,
    compute_etag,
)

__all__ = ["InMemoryMetadataStore"]


UTC = timezone.utc
DEFAULT_HOT_CAPACITY = 1024
DEFAULT_IDLE_DEMOTE_AFTER = timedelta(minutes=5)


def _idle_seconds(value: timedelta | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    else:
        seconds = float(value)
    if seconds <= 0:
        raise ValueError("idle_demote_after must be > 0 seconds")
    return seconds


def _copy_value(value: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(value)


def _copy_record(record: MetadataRecord) -> MetadataRecord:
    return replace(record, value=_copy_value(record.value))


class InMemoryMetadataStore:
    """Free-threading-safe in-memory :class:`MetadataStore`.

    ``hot_capacity`` controls hot-key compaction: when :meth:`compact` sees
    more hot records than the capacity allows, it demotes the least recently
    accessed hot records first. ``idle_demote_after`` controls idle demotion;
    set it to ``None`` to disable age-based demotion.
    """

    def __init__(
        self,
        *,
        hot_capacity: int = DEFAULT_HOT_CAPACITY,
        idle_demote_after: timedelta | float | int | None = (
            DEFAULT_IDLE_DEMOTE_AFTER
        ),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if hot_capacity <= 0:
            raise ValueError("hot_capacity must be > 0")
        self._lock = threading.RLock()
        self._hot: dict[str, dict[str, MetadataRecord]] = {}
        self._cold: dict[str, dict[str, MetadataRecord]] = {}
        self._hot_capacity = hot_capacity
        self._idle_demote_seconds = _idle_seconds(idle_demote_after)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def put(
        self,
        key: str,
        value: dict[str, Any],
        *,
        tier: Tier = Tier.HOT,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Insert or update a tenant-scoped record."""

        now = self._now()
        copied = _copy_value(value)
        etag = compute_etag(copied)
        with self._lock:
            found = self._find_locked(key, tenant_id=tenant_id)
            if found is not None:
                _, bucket, current = found
                record = replace(
                    current,
                    value=copied,
                    updated_at=now,
                    accessed_at=now,
                    etag=etag,
                )
            else:
                bucket = self._bucket_locked(tier, tenant_id)
                record = MetadataRecord(
                    key=key,
                    value=copied,
                    tier=tier,
                    tenant_id=tenant_id,
                    created_at=now,
                    updated_at=now,
                    accessed_at=now,
                    etag=etag,
                )
            bucket[key] = record
            return _copy_record(record)

    async def get(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord:
        """Return a record and bump its access timestamp."""

        record = await self.get_or_none(key, tenant_id=tenant_id)
        if record is None:
            raise KeyNotFoundError(key)
        return record

    async def get_or_none(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord | None:
        """Return a record, or ``None`` when absent."""

        now = self._now()
        with self._lock:
            found = self._find_locked(key, tenant_id=tenant_id)
            if found is None:
                return None
            _, bucket, current = found
            record = replace(current, accessed_at=now)
            bucket[key] = record
            return _copy_record(record)

    async def delete(self, key: str, *, tenant_id: str = "default") -> bool:
        """Delete a record from either tier."""

        with self._lock:
            hot = self._hot.get(tenant_id)
            if hot is not None and key in hot:
                del hot[key]
                self._drop_empty_tenant_locked(tenant_id)
                return True

            cold = self._cold.get(tenant_id)
            if cold is not None and key in cold:
                del cold[key]
                self._drop_empty_tenant_locked(tenant_id)
                return True
        return False

    async def scan_prefix(
        self,
        prefix: str,
        *,
        tenant_id: str = "default",
        limit: int = 100,
    ) -> list[MetadataRecord]:
        """Return tenant records matching ``prefix`` in lexicographic order."""

        if limit <= 0:
            return []

        now = self._now()
        with self._lock:
            matches: list[tuple[str, Tier, MetadataRecord]] = []
            for tier, bucket in (
                (Tier.HOT, self._hot.get(tenant_id, {})),
                (Tier.COLD, self._cold.get(tenant_id, {})),
            ):
                for key, record in bucket.items():
                    if key.startswith(prefix):
                        matches.append((key, tier, record))

            records: list[MetadataRecord] = []
            for key, tier, current in sorted(matches, key=lambda item: item[0])[
                :limit
            ]:
                bucket = self._bucket_locked(tier, tenant_id)
                record = replace(current, accessed_at=now)
                bucket[key] = record
                records.append(_copy_record(record))
            return records

    async def promote(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord:
        """Move a record to the hot tier."""

        now = self._now()
        with self._lock:
            hot = self._hot.get(tenant_id, {})
            if key in hot:
                return _copy_record(hot[key])

            cold = self._cold.get(tenant_id, {})
            current = cold.get(key)
            if current is None:
                raise KeyNotFoundError(key)

            del cold[key]
            record = replace(
                current,
                tier=Tier.HOT,
                updated_at=now,
                accessed_at=now,
            )
            self._bucket_locked(Tier.HOT, tenant_id)[key] = record
            self._drop_empty_tenant_locked(tenant_id)
            return _copy_record(record)

    async def demote(
        self, key: str, *, tenant_id: str = "default"
    ) -> MetadataRecord:
        """Move a record to the cold tier."""

        now = self._now()
        with self._lock:
            cold = self._cold.get(tenant_id, {})
            if key in cold:
                return _copy_record(cold[key])

            hot = self._hot.get(tenant_id, {})
            current = hot.get(key)
            if current is None:
                raise KeyNotFoundError(key)

            del hot[key]
            record = replace(
                current,
                tier=Tier.COLD,
                updated_at=now,
                accessed_at=now,
            )
            self._bucket_locked(Tier.COLD, tenant_id)[key] = record
            self._drop_empty_tenant_locked(tenant_id)
            return _copy_record(record)

    async def compact(self, *, tenant_id: str = "default") -> int:
        """Demote idle or over-capacity hot records for one tenant."""

        now = self._now()
        moved = 0
        with self._lock:
            hot = self._hot.get(tenant_id)
            if not hot:
                return 0

            for key, record in list(hot.items()):
                if self._is_idle(record, now=now):
                    self._demote_locked(key, record, tenant_id=tenant_id, now=now)
                    moved += 1

            hot = self._hot.get(tenant_id)
            if not hot:
                return moved

            overflow = len(hot) - self._hot_capacity
            if overflow <= 0:
                return moved

            ordered = sorted(
                hot.items(),
                key=lambda item: (item[1].accessed_at, item[0]),
            )
            for key, record in ordered[:overflow]:
                self._demote_locked(key, record, tenant_id=tenant_id, now=now)
                moved += 1
        return moved

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            return now.replace(tzinfo=UTC)
        return now

    def _bucket_locked(
        self, tier: Tier, tenant_id: str
    ) -> dict[str, MetadataRecord]:
        if tier is Tier.HOT:
            return self._hot.setdefault(tenant_id, {})
        return self._cold.setdefault(tenant_id, {})

    def _find_locked(
        self, key: str, *, tenant_id: str
    ) -> tuple[Tier, dict[str, MetadataRecord], MetadataRecord] | None:
        hot = self._hot.get(tenant_id)
        if hot is not None and key in hot:
            return Tier.HOT, hot, hot[key]

        cold = self._cold.get(tenant_id)
        if cold is not None and key in cold:
            return Tier.COLD, cold, cold[key]
        return None

    def _is_idle(self, record: MetadataRecord, *, now: datetime) -> bool:
        if self._idle_demote_seconds is None:
            return False
        return (now - record.accessed_at).total_seconds() >= (
            self._idle_demote_seconds
        )

    def _demote_locked(
        self,
        key: str,
        record: MetadataRecord,
        *,
        tenant_id: str,
        now: datetime,
    ) -> None:
        hot = self._hot.get(tenant_id)
        if hot is not None:
            hot.pop(key, None)
        demoted = replace(record, tier=Tier.COLD, updated_at=now)
        self._bucket_locked(Tier.COLD, tenant_id)[key] = demoted
        self._drop_empty_tenant_locked(tenant_id)

    def _drop_empty_tenant_locked(self, tenant_id: str) -> None:
        hot = self._hot.get(tenant_id)
        if hot is not None and not hot:
            self._hot.pop(tenant_id, None)

        cold = self._cold.get(tenant_id)
        if cold is not None and not cold:
            self._cold.pop(tenant_id, None)
