"""Redis-backed MetadataStore implementation.

Stores all records as Redis Hashes keyed ``{key_prefix}{tenant_id}:{key}``.
All records are physically in Redis (always HOT latency), but the ``tier``
field is tracked logically so callers can observe demotions and the
``compact()`` sweep can auto-demote idle entries.

Thread-safety
~~~~~~~~~~~~~
``_lock`` (``threading.RLock``) guards lazy ``_client`` initialisation.
No lock is held across an ``await``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from ravi.kernel.metadata import (
    KeyNotFoundError,
    MetadataRecord,
    Tier,
    compute_etag,
)

__all__ = ["RedisMetadataStore"]

UTC = timezone.utc

# Hash field names kept short to reduce memory overhead.
_F_VALUE = "value_json"
_F_TIER = "tier"
_F_CREATED = "created_at"
_F_UPDATED = "updated_at"
_F_ACCESSED = "accessed_at"
_F_ETAG = "etag"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _record_from_hash(
    key: str,
    tenant_id: str,
    data: dict[bytes | str, bytes | str],
) -> MetadataRecord:
    import json

    def _str(v: bytes | str) -> str:
        return v.decode() if isinstance(v, bytes) else v

    value: dict[str, Any] = json.loads(_str(data[_F_VALUE]))
    tier_raw = _str(data[_F_TIER])
    tier = Tier.HOT if tier_raw == Tier.HOT.value else Tier.COLD
    return MetadataRecord(
        key=key,
        value=value,
        tier=tier,
        tenant_id=tenant_id,
        created_at=_parse_dt(_str(data[_F_CREATED])),
        updated_at=_parse_dt(_str(data[_F_UPDATED])),
        accessed_at=_parse_dt(_str(data[_F_ACCESSED])),
        etag=_str(data[_F_ETAG]),
    )


class RedisMetadataStore:
    """Redis-backed :class:`MetadataStore`.

    Parameters
    ----------
    redis_url:
        Redis connection URL.
    key_prefix:
        String prepended to every Redis key: ``{key_prefix}{tenant_id}:{key}``.
    idle_demote_seconds:
        How many seconds of idle time (no ``get`` or ``scan_prefix``) before
        :meth:`compact` logically demotes a HOT record to COLD.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        *,
        key_prefix: str = "meta:",
        idle_demote_seconds: float = 300.0,
    ) -> None:
        self._url = redis_url
        self._key_prefix = key_prefix
        self._idle_demote_seconds = idle_demote_seconds
        self._lock = threading.RLock()
        self._client: aioredis.Redis | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _redis(self) -> aioredis.Redis:  # type: ignore[type-arg]
        with self._lock:
            if self._client is None:
                self._client = aioredis.from_url(
                    self._url, decode_responses=True
                )
        return self._client

    def _rkey(self, tenant_id: str, key: str) -> str:
        return f"{self._key_prefix}{tenant_id}:{key}"

    def _tenant_scan_pattern(self, tenant_id: str, prefix: str) -> str:
        return f"{self._key_prefix}{tenant_id}:{prefix}*"

    def _extract_key(self, tenant_id: str, redis_key: str) -> str:
        """Strip ``{key_prefix}{tenant_id}:`` from a full Redis key."""
        strip = f"{self._key_prefix}{tenant_id}:"
        if redis_key.startswith(strip):
            return redis_key[len(strip):]
        return redis_key

    # ------------------------------------------------------------------
    # MetadataStore Protocol
    # ------------------------------------------------------------------

    async def put(
        self,
        key: str,
        value: dict[str, Any],
        *,
        tier: Tier = Tier.HOT,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Insert or update a record; preserves ``created_at`` on update."""
        import json

        client = await self._redis()
        rkey = self._rkey(tenant_id, key)
        now = _now()
        etag = compute_etag(value)
        value_json = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

        existing = await client.hgetall(rkey)
        if existing:
            created_at_str = existing[_F_CREATED]
            tier_raw = existing[_F_TIER]
            # Keep the stored tier (in-place update)
            effective_tier = Tier.HOT if tier_raw == Tier.HOT.value else Tier.COLD
        else:
            created_at_str = now.isoformat()
            effective_tier = tier

        await client.hset(
            rkey,
            mapping={
                _F_VALUE: value_json,
                _F_TIER: effective_tier.value,
                _F_CREATED: created_at_str,
                _F_UPDATED: now.isoformat(),
                _F_ACCESSED: now.isoformat(),
                _F_ETAG: etag,
            },
        )
        return MetadataRecord(
            key=key,
            value=value,
            tier=effective_tier,
            tenant_id=tenant_id,
            created_at=_parse_dt(created_at_str),
            updated_at=now,
            accessed_at=now,
            etag=etag,
        )

    async def get(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Return the record; raise :class:`KeyNotFoundError` when absent."""
        record = await self.get_or_none(key, tenant_id=tenant_id)
        if record is None:
            raise KeyNotFoundError(key)
        return record

    async def get_or_none(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord | None:
        """Return the record or ``None`` when absent."""
        client = await self._redis()
        rkey = self._rkey(tenant_id, key)
        data = await client.hgetall(rkey)
        if not data:
            return None
        now = _now()
        await client.hset(rkey, mapping={_F_ACCESSED: now.isoformat()})
        record = _record_from_hash(key, tenant_id, data)
        # Return with the freshly bumped accessed_at
        from dataclasses import replace
        return replace(record, accessed_at=now)

    async def delete(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> bool:
        """Remove ``key``; return ``True`` if something was deleted."""
        client = await self._redis()
        rkey = self._rkey(tenant_id, key)
        exists = await client.exists(rkey)
        if not exists:
            return False
        await client.delete(rkey)
        return True

    async def scan_prefix(
        self,
        prefix: str,
        *,
        tenant_id: str = "default",
        limit: int = 100,
    ) -> list[MetadataRecord]:
        """Return records whose key starts with ``prefix``, lexicographically sorted."""
        if limit <= 0:
            return []
        client = await self._redis()
        pattern = self._tenant_scan_pattern(tenant_id, prefix)
        matched_keys: list[str] = []
        cursor: int = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            matched_keys.extend(keys)
            if cursor == 0:
                break
            if len(matched_keys) >= limit:
                break
        matched_keys = sorted(matched_keys)[:limit]

        now = _now()
        records: list[MetadataRecord] = []
        for rkey in matched_keys:
            data = await client.hgetall(rkey)
            if not data:
                continue
            await client.hset(rkey, mapping={_F_ACCESSED: now.isoformat()})
            key = self._extract_key(tenant_id, rkey)
            record = _record_from_hash(key, tenant_id, data)
            from dataclasses import replace
            records.append(replace(record, accessed_at=now))
        return records

    async def promote(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Move ``key`` to :attr:`Tier.HOT`."""
        client = await self._redis()
        rkey = self._rkey(tenant_id, key)
        data = await client.hgetall(rkey)
        if not data:
            raise KeyNotFoundError(key)
        now = _now()
        await client.hset(
            rkey,
            mapping={
                _F_TIER: Tier.HOT.value,
                _F_UPDATED: now.isoformat(),
                _F_ACCESSED: now.isoformat(),
            },
        )
        record = _record_from_hash(key, tenant_id, data)
        from dataclasses import replace
        return replace(record, tier=Tier.HOT, updated_at=now, accessed_at=now)

    async def demote(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Move ``key`` to :attr:`Tier.COLD`."""
        client = await self._redis()
        rkey = self._rkey(tenant_id, key)
        data = await client.hgetall(rkey)
        if not data:
            raise KeyNotFoundError(key)
        now = _now()
        await client.hset(
            rkey,
            mapping={
                _F_TIER: Tier.COLD.value,
                _F_UPDATED: now.isoformat(),
                _F_ACCESSED: now.isoformat(),
            },
        )
        record = _record_from_hash(key, tenant_id, data)
        from dataclasses import replace
        return replace(record, tier=Tier.COLD, updated_at=now, accessed_at=now)

    async def compact(
        self,
        *,
        tenant_id: str = "default",
    ) -> int:
        """Scan all tenant keys; demote HOT records idle > ``idle_demote_seconds``."""
        client = await self._redis()
        pattern = self._tenant_scan_pattern(tenant_id, "")
        now = _now()
        demoted = 0

        cursor: int = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            for rkey in keys:
                data = await client.hgetall(rkey)
                if not data:
                    continue
                tier_raw = data.get(_F_TIER, "")
                if tier_raw != Tier.HOT.value:
                    continue
                accessed_raw = data.get(_F_ACCESSED, "")
                if not accessed_raw:
                    continue
                accessed_at = _parse_dt(accessed_raw)
                idle = (now - accessed_at).total_seconds()
                if idle >= self._idle_demote_seconds:
                    await client.hset(
                        rkey,
                        mapping={
                            _F_TIER: Tier.COLD.value,
                            _F_UPDATED: now.isoformat(),
                        },
                    )
                    demoted += 1
            if cursor == 0:
                break
        return demoted
