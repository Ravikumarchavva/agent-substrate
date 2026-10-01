"""The SQLite runtime store, held to the shared conformance suite."""

from __future__ import annotations

import pytest

from substrate.kernel.runtime.sqlite_store import SqliteRuntimeStore
from substrate.kernel.testing.conformance.runtime_store import NOW, RuntimeStoreConformance


class TestSqliteRuntimeStore(RuntimeStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = SqliteRuntimeStore(tmp_path / "runtime.sqlite3", clock=lambda: NOW)
        await store.start()
        yield store
        await store.aclose()


class TestSqliteRuntimeStoreInMemory(RuntimeStoreConformance):
    """``:memory:`` goes through the same code, so tests need no second implementation."""

    @pytest.fixture
    async def store(self):
        store = SqliteRuntimeStore(":memory:", clock=lambda: NOW)
        await store.start()
        yield store
        await store.aclose()
