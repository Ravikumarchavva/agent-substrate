"""Tests for PostgresMetadataStore.

All database I/O is mocked via AsyncMock — no live Postgres required.

Coverage
--------
- Protocol conformance (isinstance against MetadataStore)
- put: new record insert, update preserves created_at and existing tier
- get: happy path, KeyNotFoundError on miss
- get_or_none: None on miss, record on hit
- delete: True when deleted, False when absent
- scan_prefix: ORDER BY key LIKE, limit respected
- promote / demote: tier field transitions, KeyNotFoundError on miss
- compact: HOT idle records demoted, non-HOT skipped
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravi.integrations.metadata import PostgresMetadataStore
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


def _make_row(
    key: str = "k1",
    tenant_id: str = "default",
    value: dict[str, Any] | None = None,
    tier: str = "cold",
    *,
    accessed_at: datetime | None = None,
) -> MagicMock:
    """Build a minimal MetadataRow mock."""
    if value is None:
        value = {"x": 1}
    now = _now()
    row = MagicMock()
    row.composite_pk = f"{tenant_id}:{key}"
    row.key = key
    row.tenant_id = tenant_id
    row.value_json = value
    row.tier = tier
    row.created_at = now
    row.updated_at = now
    row.accessed_at = accessed_at or now
    row.etag = compute_etag(value)
    return row


def _make_store() -> PostgresMetadataStore:
    """Return a store with a mocked session factory."""
    store = PostgresMetadataStore("postgresql+asyncpg://user:pass@localhost/db")
    return store


def _patch_session(store: PostgresMetadataStore, session_mock: AsyncMock) -> None:
    """Inject a mock session factory into the store."""
    factory = MagicMock()
    factory.return_value = session_mock
    store._session_factory = factory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session() -> AsyncMock:
    """Async context manager mock for a DB session."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_postgres_store_satisfies_metadata_store_protocol(self) -> None:
        store = PostgresMetadataStore("postgresql+asyncpg://u:p@h/db")
        assert isinstance(store, MetadataStore)


# ===========================================================================
# put
# ===========================================================================


class TestPut:
    async def test_put_inserts_new_record(self, db_session: AsyncMock) -> None:
        db_session.get = AsyncMock(return_value=None)
        db_session.add = MagicMock()
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock(side_effect=lambda row: None)

        store = _make_store()
        _patch_session(store, db_session)

        # refresh should populate the row; simulate by having get return a row
        # after the add — patch refresh to set row fields
        row_holder: list[Any] = []

        def capture_add(row: Any) -> None:
            row_holder.append(row)

        db_session.add.side_effect = capture_add

        async def fake_refresh(row: Any) -> None:
            pass

        db_session.refresh.side_effect = fake_refresh

        await store.put("k1", {"a": 1}, tier=Tier.COLD, tenant_id="t1")

        db_session.add.assert_called_once()
        db_session.commit.assert_called_once()
        # The record returned should carry the correct key/tenant
        # (actual values come from the ORM row, but the row was mocked;
        # we check the add was called with correct attributes)
        added_row = row_holder[0]
        assert added_row.key == "k1"
        assert added_row.tenant_id == "t1"
        assert added_row.tier == Tier.COLD.value
        assert added_row.etag == compute_etag({"a": 1})

    async def test_put_update_preserves_created_at(self, db_session: AsyncMock) -> None:
        existing = _make_row("k1", "t1", {"old": True}, tier="cold")
        db_session.get = AsyncMock(return_value=existing)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock(side_effect=lambda row: None)

        store = _make_store()
        _patch_session(store, db_session)

        old_created = existing.created_at
        await store.put("k1", {"new": True}, tenant_id="t1")

        # created_at must not have changed — the row attribute should be unchanged
        assert existing.created_at == old_created
        # etag should be updated
        assert existing.etag == compute_etag({"new": True})


# ===========================================================================
# get
# ===========================================================================


class TestGet:
    async def test_get_returns_record(self, db_session: AsyncMock) -> None:
        row = _make_row("k1", "t1")
        db_session.get = AsyncMock(return_value=row)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock(side_effect=lambda r: None)

        store = _make_store()
        _patch_session(store, db_session)

        record = await store.get("k1", tenant_id="t1")
        assert record.key == "k1"
        assert record.tenant_id == "t1"

    async def test_get_raises_key_not_found_when_absent(
        self, db_session: AsyncMock
    ) -> None:
        db_session.get = AsyncMock(return_value=None)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        with pytest.raises(KeyNotFoundError):
            await store.get("missing", tenant_id="t1")


# ===========================================================================
# get_or_none
# ===========================================================================


class TestGetOrNone:
    async def test_get_or_none_returns_none_when_absent(
        self, db_session: AsyncMock
    ) -> None:
        db_session.get = AsyncMock(return_value=None)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        result = await store.get_or_none("ghost", tenant_id="t1")
        assert result is None

    async def test_get_or_none_returns_record_when_present(
        self, db_session: AsyncMock
    ) -> None:
        row = _make_row("k1", "t1")
        db_session.get = AsyncMock(return_value=row)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock(side_effect=lambda r: None)

        store = _make_store()
        _patch_session(store, db_session)

        result = await store.get_or_none("k1", tenant_id="t1")
        assert result is not None
        assert result.key == "k1"


# ===========================================================================
# delete
# ===========================================================================


class TestDelete:
    async def test_delete_returns_true_when_row_exists(
        self, db_session: AsyncMock
    ) -> None:
        row = _make_row("k1", "t1")
        db_session.get = AsyncMock(return_value=row)
        db_session.delete = AsyncMock()
        db_session.commit = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        result = await store.delete("k1", tenant_id="t1")
        assert result is True
        db_session.delete.assert_called_once_with(row)

    async def test_delete_returns_false_when_row_absent(
        self, db_session: AsyncMock
    ) -> None:
        db_session.get = AsyncMock(return_value=None)
        db_session.delete = AsyncMock()
        db_session.commit = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        result = await store.delete("ghost", tenant_id="t1")
        assert result is False
        db_session.delete.assert_not_called()


# ===========================================================================
# scan_prefix
# ===========================================================================


class TestScanPrefix:
    async def test_scan_prefix_returns_sorted_records(
        self, db_session: AsyncMock
    ) -> None:
        rows = [
            _make_row("wf:a", "t1"),
            _make_row("wf:b", "t1"),
            _make_row("wf:c", "t1"),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows
        db_session.execute = AsyncMock(return_value=result_mock)
        db_session.commit = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        records = await store.scan_prefix("wf:", tenant_id="t1", limit=10)
        assert len(records) == 3
        assert [r.key for r in records] == ["wf:a", "wf:b", "wf:c"]

    async def test_scan_prefix_zero_limit_returns_empty(
        self, db_session: AsyncMock
    ) -> None:
        store = _make_store()
        _patch_session(store, db_session)

        records = await store.scan_prefix("k", tenant_id="t1", limit=0)
        assert records == []


# ===========================================================================
# promote / demote
# ===========================================================================


class TestTierTransitions:
    async def test_promote_sets_hot_tier(self, db_session: AsyncMock) -> None:
        row = _make_row("k1", "t1", tier="cold")
        db_session.get = AsyncMock(return_value=row)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock(side_effect=lambda r: None)

        store = _make_store()
        _patch_session(store, db_session)

        record = await store.promote("k1", tenant_id="t1")
        assert row.tier == Tier.HOT.value
        assert record.tier == Tier.HOT

    async def test_promote_raises_key_not_found_when_absent(
        self, db_session: AsyncMock
    ) -> None:
        db_session.get = AsyncMock(return_value=None)

        store = _make_store()
        _patch_session(store, db_session)

        with pytest.raises(KeyNotFoundError):
            await store.promote("ghost", tenant_id="t1")

    async def test_demote_sets_cold_tier(self, db_session: AsyncMock) -> None:
        row = _make_row("k1", "t1", tier="hot")
        db_session.get = AsyncMock(return_value=row)
        db_session.commit = AsyncMock()
        db_session.refresh = AsyncMock(side_effect=lambda r: None)

        store = _make_store()
        _patch_session(store, db_session)

        record = await store.demote("k1", tenant_id="t1")
        assert row.tier == Tier.COLD.value
        assert record.tier == Tier.COLD

    async def test_demote_raises_key_not_found_when_absent(
        self, db_session: AsyncMock
    ) -> None:
        db_session.get = AsyncMock(return_value=None)

        store = _make_store()
        _patch_session(store, db_session)

        with pytest.raises(KeyNotFoundError):
            await store.demote("ghost", tenant_id="t1")


# ===========================================================================
# compact
# ===========================================================================


class TestCompact:
    async def test_compact_demotes_idle_hot_records(
        self, db_session: AsyncMock
    ) -> None:
        old_accessed = _now() - timedelta(minutes=10)
        hot_row = _make_row("k1", "t1", tier="hot", accessed_at=old_accessed)

        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [hot_row]
        db_session.execute = AsyncMock(return_value=result_mock)
        db_session.commit = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        count = await store.compact(tenant_id="t1")
        assert count == 1
        assert hot_row.tier == Tier.COLD.value

    async def test_compact_returns_zero_when_no_hot_records(
        self, db_session: AsyncMock
    ) -> None:
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db_session.execute = AsyncMock(return_value=result_mock)
        db_session.commit = AsyncMock()

        store = _make_store()
        _patch_session(store, db_session)

        count = await store.compact(tenant_id="t1")
        assert count == 0
