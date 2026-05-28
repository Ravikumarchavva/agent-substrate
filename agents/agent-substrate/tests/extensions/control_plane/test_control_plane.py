"""Tests for Section 16 — Control Plane / Multi-Region.

Coverage
--------
- Kernel contracts: RegionSpec, HotCacheEntry, FailoverDecision shapes
- InMemoryHotCache: get/put/invalidate/flush, TTL expiry, error paths
- InMemoryRegionRegistry: list, get, local, availability toggle
- LowestLatencyFallbackPolicy: picks best region, raises when none available
- Protocol conformance for all three Protocols
"""

from __future__ import annotations

import asyncio
import time

import pytest

from ravi.extensions.control_plane import (
    InMemoryHotCache,
    InMemoryRegionRegistry,
    LowestLatencyFallbackPolicy,
)
from ravi.kernel.control_plane import (
    FailoverReason,
    HotCache,
    LocalFallbackPolicy,
    RegionRegistry,
    RegionSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    rid: str,
    *,
    latency: float = 10.0,
    is_local: bool = False,
    available: bool = True,
    weight: float = 1.0,
) -> RegionSpec:
    return RegionSpec(
        region_id=rid,
        latency_ms=latency,
        weight=weight,
        is_local=is_local,
        available=available,
    )


def _registry(*specs: RegionSpec) -> InMemoryRegionRegistry:
    return InMemoryRegionRegistry(specs)


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_hot_cache_satisfies_protocol(self) -> None:
        assert isinstance(InMemoryHotCache(), HotCache)

    def test_region_registry_satisfies_protocol(self) -> None:
        reg = _registry(_spec("us-east-1"))
        assert isinstance(reg, RegionRegistry)

    def test_fallback_policy_satisfies_protocol(self) -> None:
        assert isinstance(LowestLatencyFallbackPolicy(), LocalFallbackPolicy)


# ===========================================================================
# HotCache
# ===========================================================================


class TestHotCache:
    async def test_get_miss_returns_none(self) -> None:
        cache = InMemoryHotCache()
        assert await cache.get("missing") is None

    async def test_put_and_get_returns_entry(self) -> None:
        cache = InMemoryHotCache(region_id="us-west-2")
        await cache.put("key1", b"hello")
        entry = await cache.get("key1")
        assert entry is not None
        assert entry.value == b"hello"
        assert entry.region_id == "us-west-2"
        assert entry.key == "key1"

    async def test_no_ttl_entry_does_not_expire(self) -> None:
        cache = InMemoryHotCache()
        await cache.put("k", b"v", ttl_s=None)
        entry = await cache.get("k")
        assert entry is not None
        assert entry.ttl_remaining_s is None

    async def test_ttl_entry_expires(self) -> None:
        cache = InMemoryHotCache()
        await cache.put("k", b"v", ttl_s=0.05)
        # Should be present immediately
        assert await cache.get("k") is not None
        # After expiry
        time.sleep(0.1)
        assert await cache.get("k") is None

    async def test_put_zero_ttl_raises(self) -> None:
        cache = InMemoryHotCache()
        with pytest.raises(ValueError, match="ttl_s"):
            await cache.put("k", b"v", ttl_s=0.0)

    async def test_put_negative_ttl_raises(self) -> None:
        cache = InMemoryHotCache()
        with pytest.raises(ValueError, match="ttl_s"):
            await cache.put("k", b"v", ttl_s=-1.0)

    async def test_invalidate_removes_entry(self) -> None:
        cache = InMemoryHotCache()
        await cache.put("k", b"v")
        await cache.invalidate("k")
        assert await cache.get("k") is None

    async def test_invalidate_noop_for_missing_key(self) -> None:
        cache = InMemoryHotCache()
        await cache.invalidate("ghost")  # must not raise

    async def test_flush_removes_all(self) -> None:
        cache = InMemoryHotCache()
        for i in range(5):
            await cache.put(f"k{i}", b"v")
        await cache.flush()
        for i in range(5):
            assert await cache.get(f"k{i}") is None

    async def test_put_overwrites_existing(self) -> None:
        cache = InMemoryHotCache()
        await cache.put("k", b"old")
        await cache.put("k", b"new")
        entry = await cache.get("k")
        assert entry is not None
        assert entry.value == b"new"


# ===========================================================================
# RegionRegistry
# ===========================================================================


class TestRegionRegistry:
    async def test_list_returns_all_regions(self) -> None:
        reg = _registry(_spec("r1"), _spec("r2"), _spec("r3"))
        regions = await reg.list_regions()
        assert len(regions) == 3
        ids = {r.region_id for r in regions}
        assert ids == {"r1", "r2", "r3"}

    async def test_get_returns_correct_region(self) -> None:
        reg = _registry(_spec("ap-southeast-1", latency=80.0))
        r = await reg.get_region("ap-southeast-1")
        assert r.latency_ms == 80.0

    async def test_get_unknown_raises_key_error(self) -> None:
        reg = _registry(_spec("r1"))
        with pytest.raises(KeyError):
            await reg.get_region("no-such-region")

    async def test_local_region_returns_is_local(self) -> None:
        reg = _registry(_spec("remote"), _spec("local", is_local=True))
        local = await reg.local_region()
        assert local.is_local
        assert local.region_id == "local"

    async def test_local_region_raises_when_none_is_local(self) -> None:
        reg = _registry(_spec("r1"), _spec("r2"))
        with pytest.raises(RuntimeError, match="local"):
            await reg.local_region()

    async def test_mark_unavailable(self) -> None:
        reg = _registry(_spec("r1"))
        await reg.mark_unavailable("r1")
        r = await reg.get_region("r1")
        assert not r.available

    async def test_mark_available_restores(self) -> None:
        reg = _registry(_spec("r1", available=False))
        await reg.mark_available("r1")
        r = await reg.get_region("r1")
        assert r.available

    async def test_mark_unknown_region_is_noop(self) -> None:
        reg = _registry(_spec("r1"))
        await reg.mark_unavailable("ghost")  # must not raise
        await reg.mark_available("ghost")  # must not raise


# ===========================================================================
# LocalFallbackPolicy
# ===========================================================================


class TestLowestLatencyFallbackPolicy:
    async def test_picks_lowest_latency(self) -> None:
        policy = LowestLatencyFallbackPolicy()
        regions = [
            _spec("slow", latency=100.0),
            _spec("fast", latency=5.0),
            _spec("medium", latency=40.0),
        ]
        decision = await policy.decide_fallback(
            "primary",
            regions,
            reason=FailoverReason.UNREACHABLE,
        )
        assert decision.fallback_region_id == "fast"
        assert decision.original_region_id == "primary"
        assert decision.reason is FailoverReason.UNREACHABLE

    async def test_excludes_unavailable_regions(self) -> None:
        policy = LowestLatencyFallbackPolicy()
        regions = [
            _spec("ok", latency=20.0, available=True),
            _spec("down", latency=5.0, available=False),
        ]
        decision = await policy.decide_fallback(
            "primary", regions, reason=FailoverReason.HIGH_LATENCY
        )
        assert decision.fallback_region_id == "ok"

    async def test_excludes_the_unavailable_region_itself(self) -> None:
        """The unavailable region should not be selected as fallback."""
        policy = LowestLatencyFallbackPolicy()
        regions = [
            _spec("primary", latency=1.0, available=True),  # lowest latency
            _spec("backup", latency=50.0, available=True),
        ]
        decision = await policy.decide_fallback(
            "primary", regions, reason=FailoverReason.UNREACHABLE
        )
        assert decision.fallback_region_id == "backup"

    async def test_no_available_regions_raises(self) -> None:
        policy = LowestLatencyFallbackPolicy()
        regions = [_spec("down", available=False)]
        with pytest.raises(RuntimeError, match="fallback"):
            await policy.decide_fallback(
                "primary", regions, reason=FailoverReason.UNREACHABLE
            )

    async def test_empty_available_list_raises(self) -> None:
        policy = LowestLatencyFallbackPolicy()
        with pytest.raises(RuntimeError):
            await policy.decide_fallback(
                "primary", [], reason=FailoverReason.EXPLICIT_DRAIN
            )

    async def test_decision_has_timestamp(self) -> None:
        policy = LowestLatencyFallbackPolicy()
        decision = await policy.decide_fallback(
            "primary",
            [_spec("backup")],
            reason=FailoverReason.UNREACHABLE,
        )
        assert "T" in decision.decided_at  # ISO-8601 contains 'T'
