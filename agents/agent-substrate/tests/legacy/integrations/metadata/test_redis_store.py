"""Tests for RedisMetadataStore.

All Redis I/O is mocked — no live Redis server required.

Coverage
--------
- Protocol conformance (isinstance check against MetadataStore)
- put: new record, update preserves created_at, etag recomputed
- get: happy path, KeyNotFoundError on miss
- get_or_none: returns None on miss, updates accessed_at
- delete: returns True when deleted, False when absent
- scan_prefix: sorted results, limit respected, empty prefix
- promote / demote: tier field updated
- compact: idles HOT records past threshold are demoted, non-HOT skipped
- lazy client init via patch
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ravi.integrations.metadata import RedisMetadataStore
from ravi.kernel.metadata import (
    KeyNotFoundError,
    MetadataStore,
    Tier,
    compute_etag,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _make_hash(
    key: str = "k1",
    tenant_id: str = "default",
    value: dict[str, Any] | None = None,
    tier: str = "hot",
    *,
    accessed_at: datetime | None = None,
) -> dict[str, str]:
    """Build a minimal Redis hash dict as returned by HGETALL."""
    if value is None:
        value = {"x": 1}
    now = _now()
    at = accessed_at or now
    return {
        "value_json": json.dumps(value),
        "tier": tier,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "accessed_at": at.isoformat(),
        "etag": compute_etag(value),
    }


def _make_store(mock_client: AsyncMock) -> RedisMetadataStore:
    store = RedisMetadataStore(redis_url="redis://localhost:6379/0", key_prefix="meta:")
    store._client = mock_client
    return store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock()


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_redis_store_satisfies_metadata_store_protocol(self) -> None:
        store = RedisMetadataStore()
        assert isinstance(store, MetadataStore)


# ===========================================================================
# put
# ===========================================================================


class TestPut:
    async def test_put_new_record_calls_hset(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        mock_client.hset = AsyncMock(return_value=1)
        store = _make_store(mock_client)

        record = await store.put("k1", {"a": 1}, tier=Tier.HOT, tenant_id="t1")

        assert record.key == "k1"
        assert record.tenant_id == "t1"
        assert record.tier == Tier.HOT
        assert record.value == {"a": 1}
        assert record.etag == compute_etag({"a": 1})
        mock_client.hset.assert_called_once()
        call_kwargs = mock_client.hset.call_args
        assert call_kwargs[0][0] == "meta:t1:k1"

    async def test_put_update_preserves_created_at(self, mock_client: AsyncMock) -> None:
        old_created = "2024-01-01T00:00:00+00:00"
        existing = _make_hash("k1", "t1")
        existing["created_at"] = old_created
        mock_client.hgetall = AsyncMock(return_value=existing)
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        record = await store.put("k1", {"b": 2}, tenant_id="t1")

        assert record.created_at.isoformat() == old_created
        assert record.value == {"b": 2}

    async def test_put_recomputes_etag(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        mock_client.hset = AsyncMock(return_value=1)
        store = _make_store(mock_client)

        value = {"nested": {"z": 99}}
        record = await store.put("k2", value, tenant_id="default")
        assert record.etag == compute_etag(value)

    async def test_put_cold_tier_stored(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        mock_client.hset = AsyncMock(return_value=1)
        store = _make_store(mock_client)

        record = await store.put("k3", {"c": 3}, tier=Tier.COLD, tenant_id="t2")
        assert record.tier == Tier.COLD


# ===========================================================================
# get
# ===========================================================================


class TestGet:
    async def test_get_returns_record(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(return_value=_make_hash("k1", "t1"))
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        record = await store.get("k1", tenant_id="t1")

        assert record.key == "k1"
        assert record.tenant_id == "t1"

    async def test_get_raises_key_not_found_when_absent(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        with pytest.raises(KeyNotFoundError):
            await store.get("missing", tenant_id="t1")

    async def test_get_bumps_accessed_at(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(return_value=_make_hash("k1", "t1"))
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        await store.get("k1", tenant_id="t1")

        # hset should be called to update accessed_at
        mock_client.hset.assert_called_once()


# ===========================================================================
# get_or_none
# ===========================================================================


class TestGetOrNone:
    async def test_get_or_none_returns_none_when_absent(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        result = await store.get_or_none("ghost", tenant_id="t1")
        assert result is None

    async def test_get_or_none_returns_record_when_present(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.hgetall = AsyncMock(return_value=_make_hash("k1", "t1"))
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        result = await store.get_or_none("k1", tenant_id="t1")
        assert result is not None
        assert result.key == "k1"


# ===========================================================================
# delete
# ===========================================================================


class TestDelete:
    async def test_delete_returns_true_when_key_existed(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.exists = AsyncMock(return_value=1)
        mock_client.delete = AsyncMock(return_value=1)
        store = _make_store(mock_client)

        result = await store.delete("k1", tenant_id="t1")
        assert result is True
        mock_client.delete.assert_called_once_with("meta:t1:k1")

    async def test_delete_returns_false_when_key_absent(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.exists = AsyncMock(return_value=0)
        mock_client.delete = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        result = await store.delete("ghost", tenant_id="t1")
        assert result is False
        mock_client.delete.assert_not_called()


# ===========================================================================
# scan_prefix
# ===========================================================================


class TestScanPrefix:
    async def test_scan_prefix_returns_sorted_records(
        self, mock_client: AsyncMock
    ) -> None:
        keys = ["meta:t1:wf:b", "meta:t1:wf:a", "meta:t1:wf:c"]

        async def mock_scan(
            cursor: int = 0, match: str = "", count: int = 100
        ) -> tuple[int, list[str]]:
            if cursor == 0:
                return (0, keys)
            return (0, [])

        mock_client.scan = mock_scan
        mock_client.hgetall = AsyncMock(
            side_effect=lambda rkey: _make_hash(rkey, "t1")
        )
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        records = await store.scan_prefix("wf:", tenant_id="t1", limit=10)

        # The store strips the "{key_prefix}{tenant_id}:" prefix from redis keys
        assert [r.key for r in records] == ["wf:a", "wf:b", "wf:c"]

    async def test_scan_prefix_limit_respected(self, mock_client: AsyncMock) -> None:
        keys = [f"meta:t1:k{i}" for i in range(10)]

        async def mock_scan(
            cursor: int = 0, match: str = "", count: int = 100
        ) -> tuple[int, list[str]]:
            return (0, keys)

        mock_client.scan = mock_scan
        mock_client.hgetall = AsyncMock(
            side_effect=lambda rkey: _make_hash(rkey, "t1")
        )
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        records = await store.scan_prefix("k", tenant_id="t1", limit=3)
        assert len(records) == 3

    async def test_scan_prefix_zero_limit_returns_empty(
        self, mock_client: AsyncMock
    ) -> None:
        store = _make_store(mock_client)
        records = await store.scan_prefix("k", tenant_id="t1", limit=0)
        assert records == []


# ===========================================================================
# promote / demote
# ===========================================================================


class TestTierTransitions:
    async def test_promote_sets_hot_tier(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(
            return_value=_make_hash("k1", "t1", tier="cold")
        )
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        record = await store.promote("k1", tenant_id="t1")
        assert record.tier == Tier.HOT

    async def test_promote_raises_key_not_found_when_absent(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        store = _make_store(mock_client)

        with pytest.raises(KeyNotFoundError):
            await store.promote("ghost", tenant_id="t1")

    async def test_demote_sets_cold_tier(self, mock_client: AsyncMock) -> None:
        mock_client.hgetall = AsyncMock(
            return_value=_make_hash("k1", "t1", tier="hot")
        )
        mock_client.hset = AsyncMock(return_value=0)
        store = _make_store(mock_client)

        record = await store.demote("k1", tenant_id="t1")
        assert record.tier == Tier.COLD

    async def test_demote_raises_key_not_found_when_absent(
        self, mock_client: AsyncMock
    ) -> None:
        mock_client.hgetall = AsyncMock(return_value={})
        store = _make_store(mock_client)

        with pytest.raises(KeyNotFoundError):
            await store.demote("ghost", tenant_id="t1")


# ===========================================================================
# compact
# ===========================================================================


class TestCompact:
    async def test_compact_demotes_idle_hot_records(
        self, mock_client: AsyncMock
    ) -> None:
        old_time = (_now() - timedelta(seconds=400)).isoformat()
        stale_hash = _make_hash("k1", "t1", tier="hot")
        stale_hash["accessed_at"] = old_time

        async def mock_scan(
            cursor: int = 0, match: str = "", count: int = 100
        ) -> tuple[int, list[str]]:
            return (0, ["meta:t1:k1"])

        mock_client.scan = mock_scan
        mock_client.hgetall = AsyncMock(return_value=stale_hash)
        mock_client.hset = AsyncMock(return_value=0)

        store = RedisMetadataStore(idle_demote_seconds=300.0)
        store._client = mock_client

        count = await store.compact(tenant_id="t1")
        assert count == 1
        mock_client.hset.assert_called_once()

    async def test_compact_skips_cold_records(self, mock_client: AsyncMock) -> None:
        old_time = (_now() - timedelta(seconds=400)).isoformat()
        cold_hash = _make_hash("k1", "t1", tier="cold")
        cold_hash["accessed_at"] = old_time

        async def mock_scan(
            cursor: int = 0, match: str = "", count: int = 100
        ) -> tuple[int, list[str]]:
            return (0, ["meta:t1:k1"])

        mock_client.scan = mock_scan
        mock_client.hgetall = AsyncMock(return_value=cold_hash)
        mock_client.hset = AsyncMock(return_value=0)

        store = RedisMetadataStore(idle_demote_seconds=300.0)
        store._client = mock_client

        count = await store.compact(tenant_id="t1")
        assert count == 0

    async def test_compact_skips_recently_accessed_hot_records(
        self, mock_client: AsyncMock
    ) -> None:
        recent_hash = _make_hash("k1", "t1", tier="hot")
        # accessed_at is just now — not idle

        async def mock_scan(
            cursor: int = 0, match: str = "", count: int = 100
        ) -> tuple[int, list[str]]:
            return (0, ["meta:t1:k1"])

        mock_client.scan = mock_scan
        mock_client.hgetall = AsyncMock(return_value=recent_hash)
        mock_client.hset = AsyncMock(return_value=0)

        store = RedisMetadataStore(idle_demote_seconds=300.0)
        store._client = mock_client

        count = await store.compact(tenant_id="t1")
        assert count == 0

    async def test_compact_returns_zero_when_no_keys(
        self, mock_client: AsyncMock
    ) -> None:
        async def mock_scan(
            cursor: int = 0, match: str = "", count: int = 100
        ) -> tuple[int, list[str]]:
            return (0, [])

        mock_client.scan = mock_scan
        store = _make_store(mock_client)

        count = await store.compact(tenant_id="t1")
        assert count == 0


# ===========================================================================
# Lazy client init
# ===========================================================================


class TestLazyClientInit:
    async def test_client_created_lazily_on_first_use(self) -> None:
        with patch(
            "ravi.integrations.metadata._redis_store.aioredis"
        ) as mock_aioredis:
            mock_inner = AsyncMock()
            mock_inner.hgetall = AsyncMock(return_value={})
            mock_inner.hset = AsyncMock(return_value=0)
            mock_aioredis.from_url.return_value = mock_inner

            store = RedisMetadataStore(redis_url="redis://test-host:6379/1")
            assert store._client is None

            with pytest.raises(KeyNotFoundError):
                await store.get("any", tenant_id="t1")

            mock_aioredis.from_url.assert_called_once_with(
                "redis://test-host:6379/1", decode_responses=True
            )
            assert store._client is mock_inner
