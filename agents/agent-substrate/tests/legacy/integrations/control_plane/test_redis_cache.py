"""Tests for RedisHotCache.

All Redis I/O is mocked — no live Redis server required.

Coverage
--------
- Protocol conformance (isinstance check)
- get returns None on cache miss
- get returns HotCacheEntry on cache hit with TTL
- get returns HotCacheEntry on cache hit with no TTL (ttl_remaining_s=None)
- put sets key with EX when ttl_s provided
- put sets key without EX when ttl_s is None
- put raises ValueError on non-positive ttl_s
- invalidate calls DEL
- flush scans and deletes matching keys
- lazy client creation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ravi.adapters.control_plane import RedisHotCache
from ravi.kernel.control_plane._contracts import HotCache, HotCacheEntry


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    return AsyncMock()


def _make_cache(mock_client: AsyncMock) -> RedisHotCache:
    cache = RedisHotCache(
        "redis://localhost:6379/0",
        key_prefix="hotcache:",
        region_id="us-east-1",
    )
    cache._client = mock_client
    return cache


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_redis_hot_cache_is_hot_cache_protocol(
        self, mock_redis_client: AsyncMock
    ) -> None:
        cache = _make_cache(mock_redis_client)
        assert isinstance(cache, HotCache)


# ===========================================================================
# get
# ===========================================================================


class TestGet:
    async def test_get_returns_none_on_cache_miss(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.get = AsyncMock(return_value=None)
        cache = _make_cache(mock_redis_client)
        result = await cache.get("missing-key")
        assert result is None

    async def test_get_returns_entry_on_cache_hit_with_ttl(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.get = AsyncMock(return_value=b"hello")
        mock_redis_client.ttl = AsyncMock(return_value=42)
        cache = _make_cache(mock_redis_client)
        result = await cache.get("my-key")
        assert result is not None
        assert isinstance(result, HotCacheEntry)
        assert result.key == "my-key"
        assert result.value == b"hello"
        assert result.region_id == "us-east-1"
        assert result.ttl_remaining_s == 42.0

    async def test_get_returns_entry_with_no_ttl_when_ttl_minus_one(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.get = AsyncMock(return_value=b"data")
        mock_redis_client.ttl = AsyncMock(return_value=-1)
        cache = _make_cache(mock_redis_client)
        result = await cache.get("persistent-key")
        assert result is not None
        assert result.ttl_remaining_s is None

    async def test_get_calls_correct_prefixed_key(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.get = AsyncMock(return_value=None)
        cache = _make_cache(mock_redis_client)
        await cache.get("my-key")
        mock_redis_client.get.assert_called_once_with("hotcache:my-key")


# ===========================================================================
# put
# ===========================================================================


class TestPut:
    async def test_put_with_ttl_calls_set_with_ex(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.set = AsyncMock(return_value=True)
        cache = _make_cache(mock_redis_client)
        await cache.put("key1", b"value1", ttl_s=30.0)
        mock_redis_client.set.assert_called_once_with(
            "hotcache:key1", b"value1", ex=30
        )

    async def test_put_without_ttl_calls_set_without_ex(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.set = AsyncMock(return_value=True)
        cache = _make_cache(mock_redis_client)
        await cache.put("key2", b"value2")
        mock_redis_client.set.assert_called_once_with("hotcache:key2", b"value2")

    async def test_put_raises_value_error_on_zero_ttl(
        self, mock_redis_client: AsyncMock
    ) -> None:
        cache = _make_cache(mock_redis_client)
        with pytest.raises(ValueError, match="ttl_s must be positive"):
            await cache.put("key3", b"v", ttl_s=0.0)

    async def test_put_raises_value_error_on_negative_ttl(
        self, mock_redis_client: AsyncMock
    ) -> None:
        cache = _make_cache(mock_redis_client)
        with pytest.raises(ValueError):
            await cache.put("key4", b"v", ttl_s=-5.0)


# ===========================================================================
# invalidate
# ===========================================================================


class TestInvalidate:
    async def test_invalidate_calls_delete_with_prefixed_key(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.delete = AsyncMock(return_value=1)
        cache = _make_cache(mock_redis_client)
        await cache.invalidate("del-key")
        mock_redis_client.delete.assert_called_once_with("hotcache:del-key")


# ===========================================================================
# flush
# ===========================================================================


class TestFlush:
    async def test_flush_scans_and_deletes_all_matching_keys(
        self, mock_redis_client: AsyncMock
    ) -> None:
        # scan returns cursor=0 (done) and two keys on first call
        mock_redis_client.scan = AsyncMock(
            return_value=(0, [b"hotcache:k1", b"hotcache:k2"])
        )
        mock_redis_client.delete = AsyncMock(return_value=2)
        cache = _make_cache(mock_redis_client)
        await cache.flush()
        mock_redis_client.scan.assert_called_once_with(0, match="hotcache:*")
        mock_redis_client.delete.assert_called_once_with(
            b"hotcache:k1", b"hotcache:k2"
        )

    async def test_flush_noop_when_no_keys_found(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.scan = AsyncMock(return_value=(0, []))
        mock_redis_client.delete = AsyncMock()
        cache = _make_cache(mock_redis_client)
        await cache.flush()
        mock_redis_client.delete.assert_not_called()


# ===========================================================================
# Lazy client init
# ===========================================================================


class TestLazyClientInit:
    async def test_client_created_lazily_on_first_use(self) -> None:
        with patch(
            "ravi.adapters.control_plane._redis_cache.aioredis"
        ) as mock_aioredis:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=None)
            mock_aioredis.from_url.return_value = mock_client

            cache = RedisHotCache(redis_url="redis://test-host:6379/1")
            assert cache._client is None

            await cache.get("some-key")

            mock_aioredis.from_url.assert_called_once_with(
                "redis://test-host:6379/1", decode_responses=False
            )
            assert cache._client is mock_client
