"""DataRef — zero-context-bloat pointer for large data exchange between adapters.

Small data (< ``size_threshold``) is stored in Redis for speed.
Large data goes to S3/MinIO for persistence.  Both backends support TTL-based
expiry with optional pinning for long-running workflows.

Usage::

    store = DataRefStore(redis_url="redis://localhost:6379/0")
    ref = await store.store(big_csv_bytes, content_type="text/csv")
    data = await store.resolve(ref)
    await store.pin(ref)       # prevent TTL expiry
    await store.delete(ref)    # manual cleanup
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Union
from uuid import uuid4

logger = logging.getLogger("raavan.catalog.data_ref")

_DEFAULT_TTL = 3600  # 1 hour
_DEFAULT_SIZE_THRESHOLD = 1_048_576  # 1 MB
_REDIS_PREFIX = "dataref:"
_S3_PREFIX = "datarefs/"


@dataclass
class DataRef:
    """An opaque pointer to data stored in Redis or S3.

    Passed through tool results and pipeline steps without loading the
    actual data into the LLM context window.
    """

    ref_id: str = field(default_factory=lambda: str(uuid4()))
    storage: str = "redis"  # "redis" | "s3"
    key: str = ""
    size_bytes: int = 0
    content_type: str = "application/octet-stream"
    created_at: float = field(default_factory=time.time)
    ttl_seconds: int = _DEFAULT_TTL
    pinned: bool = False

    def summary(self) -> str:
        """One-line summary for inclusion in LLM context."""
        size_kb = self.size_bytes / 1024
        unit = "KB"
        if size_kb > 1024:
            size_kb /= 1024
            unit = "MB"
        return (
            f"DataRef(id={self.ref_id[:8]}..., "
            f"size={size_kb:.1f}{unit}, "
            f"type={self.content_type}, "
            f"storage={self.storage})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for JSON transport."""
        return {
            "ref_id": self.ref_id,
            "storage": self.storage,
            "key": self.key,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "pinned": self.pinned,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DataRef:
        """Reconstruct from a serialised dict."""
        return cls(
            ref_id=d["ref_id"],
            storage=d["storage"],
            key=d["key"],
            size_bytes=d["size_bytes"],
            content_type=d.get("content_type", "application/octet-stream"),
            created_at=d.get("created_at", time.time()),
            ttl_seconds=d.get("ttl_seconds", _DEFAULT_TTL),
            pinned=d.get("pinned", False),
        )


class DataRefStore:
    """Hybrid Redis/S3 store for large adapter data.

    Parameters
    ----------
    redis_url
        Redis connection string.
    s3_store
        Optional ``S3FileStore`` instance for data above the size threshold.
    size_threshold
        Byte count above which data is routed to S3 instead of Redis.
    s3_bucket
        S3 bucket name for large data (only used when *s3_store* is provided).
    """

    def __init__(
        self,
        redis_url: str,
        s3_store: Optional[Any] = None,
        size_threshold: int = _DEFAULT_SIZE_THRESHOLD,
        s3_bucket: str = "agent-datarefs",
    ) -> None:
        self._redis_url = redis_url
        self._s3 = s3_store
        self._size_threshold = size_threshold
        self._s3_bucket = s3_bucket
        self._redis: Optional[Any] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open Redis connection."""
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._redis_url, decode_responses=False)

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------
    # Store / Resolve
    # ------------------------------------------------------------------

    async def store(
        self,
        data: Union[bytes, str, dict[str, Any]],
        *,
        content_type: str = "application/octet-stream",
        ttl: int = _DEFAULT_TTL,
    ) -> DataRef:
        """Store *data* and return a ``DataRef`` pointer.

        Routes to Redis when data is below ``size_threshold``, else S3.
        """
        raw = self._to_bytes(data, content_type)
        size = len(raw)

        ref = DataRef(
            size_bytes=size,
            content_type=content_type,
            ttl_seconds=ttl,
        )

        if size <= self._size_threshold or self._s3 is None:
            ref.storage = "redis"
            ref.key = f"{_REDIS_PREFIX}{ref.ref_id}"
            await self._store_redis(ref.key, raw, ttl)
        else:
            ref.storage = "s3"
            ref.key = f"{_S3_PREFIX}{ref.ref_id}"
            await self._store_s3(ref.key, raw, content_type)
            # Also cache metadata in Redis for lookup
            await self._store_ref_meta(ref)

        logger.debug(
            "Stored DataRef %s (%d bytes → %s)", ref.ref_id[:8], size, ref.storage
        )
        return ref

    async def resolve(self, ref: DataRef) -> bytes:
        """Retrieve data pointed to by *ref*."""
        if ref.storage == "redis":
            return await self._resolve_redis(ref.key)
        elif ref.storage == "s3":
            return await self._resolve_s3(ref.key)
        else:
            raise ValueError(f"Unknown storage backend: {ref.storage}")

    async def resolve_str(self, ref: DataRef) -> str:
        """Retrieve data as a UTF-8 string."""
        raw = await self.resolve(ref)
        return raw.decode("utf-8")

    async def resolve_json(self, ref: DataRef) -> Any:
        """Retrieve data and parse as JSON."""
        raw = await self.resolve(ref)
        return json.loads(raw)

    # ------------------------------------------------------------------
    # Pin / Unpin / Delete
    # ------------------------------------------------------------------

    async def pin(self, ref: DataRef) -> None:
        """Prevent TTL expiry for this ref (makes Redis key persistent)."""
        ref.pinned = True
        if ref.storage == "redis" and self._redis is not None:
            await self._redis.persist(ref.key)

    async def unpin(self, ref: DataRef, ttl: Optional[int] = None) -> None:
        """Re-enable TTL expiry.  Uses original TTL if not specified."""
        ref.pinned = False
        if ref.storage == "redis" and self._redis is not None:
            effective_ttl = ttl if ttl is not None else ref.ttl_seconds
            await self._redis.expire(ref.key, effective_ttl)

    async def delete(self, ref: DataRef) -> None:
        """Manually remove data for a ref."""
        if ref.storage == "redis":
            if self._redis is not None:
                await self._redis.delete(ref.key)
        elif ref.storage == "s3" and self._s3 is not None:
            await self._s3.delete(self._s3_bucket, ref.key)
        # Clean metadata
        if self._redis is not None:
            await self._redis.delete(f"{_REDIS_PREFIX}meta:{ref.ref_id}")
        logger.debug("Deleted DataRef %s", ref.ref_id[:8])

    async def cleanup_expired(self) -> int:
        """Sweep and delete expired S3 refs (Redis handles its own TTL).

        Returns the number of cleaned-up refs.
        """
        if self._redis is None:
            return 0

        cleaned = 0
        cursor: Union[int, bytes] = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{_REDIS_PREFIX}meta:*", count=100
            )
            for key in keys:
                meta_raw = await self._redis.get(key)
                if meta_raw is None:
                    continue
                meta = json.loads(meta_raw)
                ref = DataRef.from_dict(meta)
                if ref.pinned:
                    continue
                age = time.time() - ref.created_at
                if age > ref.ttl_seconds:
                    await self.delete(ref)
                    cleaned += 1

            if cursor == 0:
                break

        logger.info("DataRef cleanup: removed %d expired refs", cleaned)
        return cleaned

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _store_redis(self, key: str, data: bytes, ttl: int) -> None:
        assert self._redis is not None, "DataRefStore not connected"
        await self._redis.set(key, data, ex=ttl)

    async def _resolve_redis(self, key: str) -> bytes:
        assert self._redis is not None, "DataRefStore not connected"
        data = await self._redis.get(key)
        if data is None:
            raise KeyError(f"DataRef key not found in Redis: {key}")
        return data

    async def _store_s3(self, key: str, data: bytes, content_type: str) -> None:
        assert self._s3 is not None, "S3 store not configured"
        await self._s3.put(self._s3_bucket, key, data, content_type=content_type)

    async def _resolve_s3(self, key: str) -> bytes:
        assert self._s3 is not None, "S3 store not configured"
        return await self._s3.get(self._s3_bucket, key)

    async def _store_ref_meta(self, ref: DataRef) -> None:
        """Store ref metadata in Redis for cleanup sweeps."""
        if self._redis is not None:
            meta_key = f"{_REDIS_PREFIX}meta:{ref.ref_id}"
            await self._redis.set(
                meta_key,
                json.dumps(ref.to_dict()),
                ex=ref.ttl_seconds + 60,  # keep meta slightly longer
            )

    @staticmethod
    def _to_bytes(data: Union[bytes, str, dict[str, Any]], content_type: str) -> bytes:
        """Normalise input to bytes."""
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, dict):
            return json.dumps(data).encode("utf-8")
        raise TypeError(f"Cannot convert {type(data).__name__} to bytes")
