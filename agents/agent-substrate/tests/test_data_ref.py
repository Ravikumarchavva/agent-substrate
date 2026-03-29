"""Tests for DataRef and DataRefStore."""

from __future__ import annotations

import os

import pytest
import redis as _redis_sync

from raavan.catalog._data_ref import DataRef, DataRefStore


def _redis_available() -> bool:
    """Check Redis connectivity synchronously for skip markers."""
    try:
        r = _redis_sync.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        r.ping()
        r.close()
        return True
    except Exception:
        return False


class TestDataRef:
    """DataRef dataclass tests."""

    def test_create_default(self) -> None:
        ref = DataRef()
        assert ref.storage == "redis"
        assert ref.size_bytes == 0
        assert ref.pinned is False
        assert len(ref.ref_id) == 36  # UUID format

    def test_summary(self) -> None:
        ref = DataRef(size_bytes=2048, content_type="text/csv")
        summary = ref.summary()
        assert "text/csv" in summary
        assert "KB" in summary

    def test_to_dict_roundtrip(self) -> None:
        ref = DataRef(size_bytes=100, content_type="text/plain", storage="redis")
        d = ref.to_dict()
        restored = DataRef.from_dict(d)
        assert restored.ref_id == ref.ref_id
        assert restored.size_bytes == ref.size_bytes
        assert restored.storage == ref.storage
        assert restored.content_type == ref.content_type

    def test_pinned_serialisation(self) -> None:
        ref = DataRef(pinned=True)
        d = ref.to_dict()
        assert d["pinned"] is True
        restored = DataRef.from_dict(d)
        assert restored.pinned is True


@pytest.mark.skipif(not _redis_available(), reason="Redis not available")
class TestDataRefStore:
    """DataRefStore integration tests (requires Redis)."""

    @pytest.fixture
    async def store(self) -> DataRefStore:
        s = DataRefStore(redis_url="redis://localhost:6379/0")
        await s.connect()
        yield s  # type: ignore[misc]
        await s.disconnect()

    async def test_store_and_resolve_string(self, store: DataRefStore) -> None:
        ref = await store.store("hello world", content_type="text/plain", ttl=60)
        assert ref.storage == "redis"
        assert ref.size_bytes == len("hello world".encode())

        data = await store.resolve(ref)
        assert data == b"hello world"

    async def test_store_and_resolve_bytes(self, store: DataRefStore) -> None:
        raw = b"\x00\x01\x02\x03"
        ref = await store.store(raw, content_type="application/octet-stream", ttl=60)
        assert ref.size_bytes == 4

        data = await store.resolve(ref)
        assert data == raw

    async def test_store_dict_as_json(self, store: DataRefStore) -> None:
        payload = {"key": "value", "num": 42}
        ref = await store.store(payload, content_type="application/json", ttl=60)

        import json

        data = await store.resolve(ref)
        parsed = json.loads(data)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    async def test_resolve_str_helper(self, store: DataRefStore) -> None:
        ref = await store.store("test data", content_type="text/plain", ttl=60)
        text = await store.resolve_str(ref)
        assert text == "test data"

    async def test_delete(self, store: DataRefStore) -> None:
        ref = await store.store("temporary", content_type="text/plain", ttl=60)
        await store.delete(ref)

        with pytest.raises(Exception):
            await store.resolve(ref)

    async def test_pin_and_unpin(self, store: DataRefStore) -> None:
        ref = await store.store("pinned data", content_type="text/plain", ttl=10)
        assert ref.pinned is False

        await store.pin(ref)
        assert ref.pinned is True

        await store.unpin(ref, ttl=60)
        assert ref.pinned is False
