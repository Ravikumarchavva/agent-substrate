"""In-process control plane implementations — Section 16 reference impls.

Provides:
- ``InMemoryHotCache``: dict-backed cache with per-entry TTL tracking using
  ``time.monotonic()``.  Expired entries are evicted lazily on access.
- ``InMemoryRegionRegistry``: static registry built from a list of
  ``RegionSpec`` at construction time.  Supports availability toggling.
- ``LowestLatencyFallbackPolicy``: always picks the available region with
  the lowest ``latency_ms`` as the fallback.

Thread-safety
~~~~~~~~~~~~~
All shared state is guarded by ``threading.RLock``.  No lock held across
``await``.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Sequence

from ravi.kernel.control_plane._contracts import (
    FailoverDecision,
    FailoverReason,
    HotCacheEntry,
    RegionSpec,
)

__all__ = [
    "InMemoryHotCache",
    "InMemoryRegionRegistry",
    "LowestLatencyFallbackPolicy",
]

UTC = timezone.utc


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# HotCache
# ---------------------------------------------------------------------------


class _CacheItem:
    __slots__ = ("value", "region_id", "inserted_at", "ttl_s")

    def __init__(self, value: bytes, region_id: str, ttl_s: float | None) -> None:
        self.value = value
        self.region_id = region_id
        self.inserted_at = time.monotonic()
        self.ttl_s = ttl_s

    def is_expired(self) -> bool:
        if self.ttl_s is None:
            return False
        return time.monotonic() - self.inserted_at >= self.ttl_s

    def ttl_remaining(self) -> float | None:
        if self.ttl_s is None:
            return None
        remaining = self.ttl_s - (time.monotonic() - self.inserted_at)
        return max(0.0, remaining)


class InMemoryHotCache:
    """In-process hot-path cache with lazy TTL expiry.

    Parameters
    ----------
    region_id:
        The region this cache is local to.  Included in :class:`HotCacheEntry`.
    """

    def __init__(self, *, region_id: str = "local") -> None:
        self._region_id = region_id
        self._lock = threading.RLock()
        self._store: dict[str, _CacheItem] = {}

    async def get(self, key: str) -> HotCacheEntry | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            if item.is_expired():
                del self._store[key]
                return None
            return HotCacheEntry(
                key=key,
                value=item.value,
                region_id=item.region_id,
                ttl_remaining_s=item.ttl_remaining(),
            )

    async def put(
        self, key: str, value: bytes, ttl_s: float | None = None
    ) -> None:
        if ttl_s is not None and ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive, got {ttl_s!r}")
        with self._lock:
            self._store[key] = _CacheItem(
                value=value, region_id=self._region_id, ttl_s=ttl_s
            )

    async def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    async def flush(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:  # pragma: no cover — convenience
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# RegionRegistry
# ---------------------------------------------------------------------------


class InMemoryRegionRegistry:
    """Static region registry built from a list of ``RegionSpec``.

    Parameters
    ----------
    regions:
        Initial set of regions.  At most one may have ``is_local=True``.
    """

    def __init__(self, regions: Sequence[RegionSpec]) -> None:
        self._lock = threading.RLock()
        # Mutable copies stored as dicts so we can toggle availability
        self._regions: dict[str, dict] = {}
        for r in regions:
            self._regions[r.region_id] = {
                "region_id": r.region_id,
                "latency_ms": r.latency_ms,
                "weight": r.weight,
                "is_local": r.is_local,
                "available": r.available,
            }

    def _to_spec(self, d: dict) -> RegionSpec:
        return RegionSpec(
            region_id=d["region_id"],
            latency_ms=d["latency_ms"],
            weight=d["weight"],
            is_local=d["is_local"],
            available=d["available"],
        )

    async def list_regions(self) -> Sequence[RegionSpec]:
        with self._lock:
            return [self._to_spec(d) for d in self._regions.values()]

    async def get_region(self, region_id: str) -> RegionSpec:
        with self._lock:
            d = self._regions.get(region_id)
            if d is None:
                raise KeyError(f"Unknown region: {region_id!r}")
            return self._to_spec(d)

    async def local_region(self) -> RegionSpec:
        with self._lock:
            for d in self._regions.values():
                if d["is_local"]:
                    return self._to_spec(d)
        raise RuntimeError("No region is marked as local")

    async def mark_unavailable(self, region_id: str) -> None:
        with self._lock:
            if region_id in self._regions:
                self._regions[region_id]["available"] = False

    async def mark_available(self, region_id: str) -> None:
        with self._lock:
            if region_id in self._regions:
                self._regions[region_id]["available"] = True


# ---------------------------------------------------------------------------
# LocalFallbackPolicy
# ---------------------------------------------------------------------------


class LowestLatencyFallbackPolicy:
    """Pick the available region with the lowest ``latency_ms``."""

    async def decide_fallback(
        self,
        unavailable_region_id: str,
        available_regions: Sequence[RegionSpec],
        *,
        reason: FailoverReason,
    ) -> FailoverDecision:
        candidates = [
            r for r in available_regions
            if r.available and r.region_id != unavailable_region_id
        ]
        if not candidates:
            raise RuntimeError(
                f"No available fallback region for {unavailable_region_id!r}"
            )
        best = min(candidates, key=lambda r: r.latency_ms)
        return FailoverDecision(
            original_region_id=unavailable_region_id,
            fallback_region_id=best.region_id,
            reason=reason,
            decided_at=_iso_now(),
        )
