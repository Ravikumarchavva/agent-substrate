"""Tests for the in-memory MetadataStore reference implementation."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from ravi.extensions.metadata import InMemoryMetadataStore
from ravi.kernel.metadata import (
    KeyNotFoundError,
    MetadataStore,
    Tier,
    compute_etag,
)

UTC = timezone.utc


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class TestProtocolConformance:
    async def test_isinstance_metadata_store(self) -> None:
        assert isinstance(InMemoryMetadataStore(), MetadataStore)


class TestPutAndGet:
    async def test_put_assigns_deterministic_etag_and_timestamps(self) -> None:
        clock = FakeClock()
        store = InMemoryMetadataStore(clock=clock)

        record = await store.put("agent:1", {"b": 2, "a": 1})

        assert record.etag == compute_etag({"a": 1, "b": 2})
        assert record.created_at == clock.now
        assert record.updated_at == clock.now
        assert record.accessed_at == clock.now

    async def test_tenant_isolation_for_same_key(self) -> None:
        store = InMemoryMetadataStore()

        await store.put("shared", {"tenant": "a"}, tenant_id="a")
        await store.put("shared", {"tenant": "b"}, tenant_id="b")

        a_record = await store.get("shared", tenant_id="a")
        b_record = await store.get("shared", tenant_id="b")
        assert a_record.value == {"tenant": "a"}
        assert b_record.value == {"tenant": "b"}

    async def test_update_preserves_created_at_and_existing_tier(self) -> None:
        clock = FakeClock()
        store = InMemoryMetadataStore(clock=clock)
        first = await store.put("k", {"v": 1}, tier=Tier.COLD)
        clock.advance(10)

        updated = await store.put("k", {"v": 2}, tier=Tier.HOT)

        assert updated.created_at == first.created_at
        assert updated.updated_at == clock.now
        assert updated.accessed_at == clock.now
        assert updated.tier is Tier.COLD
        assert updated.etag == compute_etag({"v": 2})

    async def test_get_bumps_accessed_at_without_changing_updated_at(self) -> None:
        clock = FakeClock()
        store = InMemoryMetadataStore(clock=clock)
        created = await store.put("k", {"v": 1})
        clock.advance(10)

        fetched = await store.get("k")

        assert fetched.created_at == created.created_at
        assert fetched.updated_at == created.updated_at
        assert fetched.accessed_at == clock.now

    async def test_returned_values_are_copied_from_store_state(self) -> None:
        store = InMemoryMetadataStore()
        original = {"nested": {"a": 1}}

        created = await store.put("k", original)
        original["nested"]["a"] = 2
        created.value["nested"]["a"] = 3

        stored = await store.get("k")
        assert stored.value == {"nested": {"a": 1}}


class TestScanPrefix:
    async def test_scan_prefix_orders_by_key_and_applies_limit(self) -> None:
        store = InMemoryMetadataStore()
        await store.put("agent:2", {"i": 2})
        await store.put("tool:1", {"i": 99})
        await store.put("agent:1", {"i": 1}, tier=Tier.COLD)
        await store.put("agent:3", {"i": 3})

        records = await store.scan_prefix("agent:", limit=2)

        assert [record.key for record in records] == ["agent:1", "agent:2"]
        assert [record.value["i"] for record in records] == [1, 2]

    async def test_scan_prefix_is_tenant_scoped(self) -> None:
        store = InMemoryMetadataStore()
        await store.put("agent:1", {"tenant": "a"}, tenant_id="a")
        await store.put("agent:2", {"tenant": "b"}, tenant_id="b")

        records = await store.scan_prefix("agent:", tenant_id="a")

        assert [record.key for record in records] == ["agent:1"]


class TestTierTransitions:
    async def test_promote_and_demote_move_between_tiers(self) -> None:
        store = InMemoryMetadataStore()
        await store.put("k", {"v": 1}, tier=Tier.COLD)

        promoted = await store.promote("k")
        assert promoted.tier is Tier.HOT

        demoted = await store.demote("k")
        assert demoted.tier is Tier.COLD

    async def test_promote_demote_missing_key_raise(self) -> None:
        store = InMemoryMetadataStore()

        with pytest.raises(KeyNotFoundError):
            await store.promote("missing")
        with pytest.raises(KeyNotFoundError):
            await store.demote("missing")


class TestDeleteAndMissing:
    async def test_delete_returns_true_once_then_false(self) -> None:
        store = InMemoryMetadataStore()
        await store.put("k", {"v": 1})

        assert await store.delete("k") is True
        assert await store.delete("k") is False
        assert await store.get_or_none("k") is None

    async def test_get_missing_key_raises_and_get_or_none_returns_none(self) -> None:
        store = InMemoryMetadataStore()

        assert await store.get_or_none("missing") is None
        with pytest.raises(KeyNotFoundError):
            await store.get("missing")


class TestCompaction:
    async def test_compact_demotes_idle_hot_records(self) -> None:
        clock = FakeClock()
        store = InMemoryMetadataStore(
            clock=clock,
            idle_demote_after=timedelta(seconds=10),
        )
        await store.put("idle", {"v": 1})
        clock.advance(5)
        await store.put("active", {"v": 2})
        clock.advance(6)

        moved = await store.compact()

        assert moved == 1
        assert (await store.get("idle")).tier is Tier.COLD
        assert (await store.get("active")).tier is Tier.HOT

    async def test_compact_demotes_least_recent_hot_keys_over_capacity(self) -> None:
        clock = FakeClock()
        store = InMemoryMetadataStore(
            clock=clock,
            hot_capacity=2,
            idle_demote_after=None,
        )
        await store.put("a", {"v": 1})
        clock.advance(1)
        await store.put("b", {"v": 2})
        clock.advance(1)
        await store.put("c", {"v": 3})

        moved = await store.compact()

        assert moved == 1
        assert (await store.get("a")).tier is Tier.COLD
        assert (await store.get("b")).tier is Tier.HOT
        assert (await store.get("c")).tier is Tier.HOT


class TestConcurrency:
    async def test_concurrent_puts_and_gets_are_consistent(self) -> None:
        store = InMemoryMetadataStore()

        def worker(worker_id: int) -> None:
            for index in range(25):
                key = f"worker:{worker_id}:{index}"
                value = {"worker": worker_id, "index": index}
                asyncio.run(store.put(key, value, tenant_id="t"))
                record = asyncio.run(store.get(key, tenant_id="t"))
                assert record.value == value

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(worker, range(8)))

        records = await store.scan_prefix("worker:", tenant_id="t", limit=1000)
        assert len(records) == 200
