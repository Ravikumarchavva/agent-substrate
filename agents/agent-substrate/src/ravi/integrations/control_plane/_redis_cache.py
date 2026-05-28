"""Redis-backed HotCache integration.

Implements :class:`ravi.kernel.control_plane._contracts.HotCache` using
``redis.asyncio``.  The client is created lazily on first use.

Thread-safety
~~~~~~~~~~~~~
``_client`` initialisation is guarded by ``threading.RLock``.  No lock is held
across ``await``.
"""

from __future__ import annotations

import threading

import redis.asyncio as aioredis

from ravi.kernel.control_plane._contracts import HotCacheEntry

__all__ = ["RedisHotCache"]


class RedisHotCache:
    """Redis-backed implementation of :class:`HotCache`.

    Parameters
    ----------
    redis_url:
        Connection URL for the Redis server.
    key_prefix:
        String prepended to all keys managed by this cache.
    region_id:
        Region identifier included in :class:`HotCacheEntry` objects.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        *,
        key_prefix: str = "hotcache:",
        region_id: str = "default",
    ) -> None:
        self._url = redis_url
        self._key_prefix = key_prefix
        self._region_id = region_id
        self._lock = threading.RLock()
        self._client: aioredis.Redis | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _redis(self) -> aioredis.Redis:
        with self._lock:
            if self._client is None:
                self._client = aioredis.from_url(
                    self._url, decode_responses=False
                )
        return self._client

    def _prefixed(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    # ------------------------------------------------------------------
    # HotCache protocol
    # ------------------------------------------------------------------

    async def get(self, key: str) -> HotCacheEntry | None:
        """Return the cached entry or ``None`` on a miss."""
        client = await self._redis()
        prefixed = self._prefixed(key)
        raw: bytes | None = await client.get(prefixed)
        if raw is None:
            return None
        ttl: int = await client.ttl(prefixed)
        ttl_remaining: float | None = float(ttl) if ttl > 0 else None
        return HotCacheEntry(
            key=key,
            value=raw,
            region_id=self._region_id,
            ttl_remaining_s=ttl_remaining,
        )

    async def put(self, key: str, value: bytes, ttl_s: float | None = None) -> None:
        """Insert or update a cache entry.

        Raise :class:`ValueError` on non-positive ``ttl_s``.
        """
        if ttl_s is not None and ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s!r}")
        client = await self._redis()
        prefixed = self._prefixed(key)
        if ttl_s is not None:
            await client.set(prefixed, value, ex=int(ttl_s))
        else:
            await client.set(prefixed, value)

    async def invalidate(self, key: str) -> None:
        """Remove an entry.  No-op when the key is absent."""
        client = await self._redis()
        await client.delete(self._prefixed(key))

    async def flush(self) -> None:
        """Remove all entries managed by this cache."""
        client = await self._redis()
        cursor: int = 0
        pattern = f"{self._key_prefix}*"
        keys_to_delete: list[bytes | str] = []
        while True:
            cursor, keys = await client.scan(cursor, match=pattern)
            keys_to_delete.extend(keys)
            if cursor == 0:
                break
        if keys_to_delete:
            await client.delete(*keys_to_delete)
