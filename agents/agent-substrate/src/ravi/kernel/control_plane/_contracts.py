"""Control Plane / Multi-Region kernel contracts — Section 16.

The control plane handles how reads, writes, and routing decisions are
distributed across multiple deployment regions.  It provides:

``RegionSpec``
    Metadata about a known region: ID, estimated round-trip latency, weight,
    and whether this is the local (co-located) region.

``RegionRegistry``
    Protocol for discovering available regions.  Implementations may read
    from Redis, a service mesh, or a static config file.

``HotCache``
    A read-through cache that fronts slow backends (Postgres, S3) with a
    fast in-process or Redis-backed store.  ``get`` returns ``None`` on a
    cache miss (callers must populate via ``put``).

``HotCacheEntry``
    Value object returned by a cache hit, including TTL metadata so callers
    can decide whether to refresh proactively.

``FailoverDecision``
    The output of :class:`LocalFallbackPolicy` when a region becomes
    unreachable.  Records the new primary and the reason for the switch.

``LocalFallbackPolicy``
    Protocol that selects a fallback region when the preferred region is
    unavailable.  Implementations may prefer the lowest-latency region,
    a pre-configured warm standby, or the local region itself.

Design constraints
------------------
* Zero concrete logic — only dataclasses, enums, and Protocols.
* No external imports — only stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, Sequence, runtime_checkable

__all__ = [
    "RegionSpec",
    "HotCacheEntry",
    "FailoverReason",
    "FailoverDecision",
    "RegionRegistry",
    "HotCache",
    "LocalFallbackPolicy",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FailoverReason(Enum):
    """Why a region failover was triggered."""

    UNREACHABLE = auto()
    """Primary region did not respond within the configured deadline."""

    HIGH_LATENCY = auto()
    """Primary region latency exceeded the acceptable ceiling."""

    EXPLICIT_DRAIN = auto()
    """Operator explicitly drained the primary region (e.g., maintenance)."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegionSpec:
    """Metadata about a known deployment region.

    Parameters
    ----------
    region_id:
        Stable human-readable identifier, e.g. ``"us-east-1"``.
    latency_ms:
        Estimated round-trip latency from this process to the region, in ms.
    weight:
        Routing weight — higher = preferred for new requests.
    is_local:
        ``True`` when this region is co-located with the current process.
        At most one region should be marked local per process.
    available:
        ``True`` when this region is considered reachable.
    """

    region_id: str
    latency_ms: float
    weight: float = 1.0
    is_local: bool = False
    available: bool = True


@dataclass(frozen=True, slots=True)
class HotCacheEntry:
    """A value retrieved from the hot cache.

    Parameters
    ----------
    key:
        Cache key.
    value:
        Cached payload.
    region_id:
        Region where this entry was cached.
    ttl_remaining_s:
        Approximate seconds until this entry expires.  ``None`` = no TTL.
    """

    key: str
    value: bytes
    region_id: str
    ttl_remaining_s: float | None = None


@dataclass(frozen=True, slots=True)
class FailoverDecision:
    """Outcome of :meth:`LocalFallbackPolicy.decide_fallback`.

    Parameters
    ----------
    original_region_id:
        The region that became unavailable.
    fallback_region_id:
        The region selected as replacement.
    reason:
        Why the failover was initiated.
    decided_at:
        ISO-8601 UTC timestamp.
    """

    original_region_id: str
    fallback_region_id: str
    reason: FailoverReason
    decided_at: str


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class RegionRegistry(Protocol):
    """Discovers and provides metadata for all known regions."""

    async def list_regions(self) -> Sequence[RegionSpec]:
        """Return all known :class:`RegionSpec` objects."""
        ...

    async def get_region(self, region_id: str) -> RegionSpec:
        """Return the :class:`RegionSpec` for ``region_id``.

        Raises :class:`KeyError` when the region is unknown.
        """
        ...

    async def local_region(self) -> RegionSpec:
        """Return the :class:`RegionSpec` for the local (co-located) region.

        Raises :class:`RuntimeError` when no region is marked local.
        """
        ...

    async def mark_unavailable(self, region_id: str) -> None:
        """Mark a region as unreachable.  Idempotent."""
        ...

    async def mark_available(self, region_id: str) -> None:
        """Mark a region as reachable again.  Idempotent."""
        ...


@runtime_checkable
class HotCache(Protocol):
    """Read-through hot-path cache.

    Keys and values are untyped bytes; serialisation is the caller's
    responsibility.  All TTLs are in seconds.
    """

    async def get(self, key: str) -> HotCacheEntry | None:
        """Return the cached entry or ``None`` on a miss."""
        ...

    async def put(self, key: str, value: bytes, ttl_s: float | None = None) -> None:
        """Insert or update a cache entry.

        ``ttl_s=None`` means the entry never expires (use with care).
        Raise :class:`ValueError` on non-positive ``ttl_s``.
        """
        ...

    async def invalidate(self, key: str) -> None:
        """Remove an entry.  No-op when the key is absent."""
        ...

    async def flush(self) -> None:
        """Remove all entries (e.g., on region failover)."""
        ...


@runtime_checkable
class LocalFallbackPolicy(Protocol):
    """Selects a fallback region when the primary becomes unavailable."""

    async def decide_fallback(
        self,
        unavailable_region_id: str,
        available_regions: Sequence[RegionSpec],
        *,
        reason: FailoverReason,
    ) -> FailoverDecision:
        """Return a :class:`FailoverDecision` given the set of available regions.

        Raises :class:`RuntimeError` when no region in ``available_regions``
        can serve as a fallback (e.g., all regions down).
        """
        ...
