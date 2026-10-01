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


async def test_closing_while_cancelled_callers_statements_are_in_flight_does_not_crash() -> None:
    """A task cancelled mid-statement does not stop the statement: its thread finishes it.
    Closing the connection under that thread is a segfault, not an exception, so close
    must queue behind in-flight work. Repeated, because the window is narrow."""
    import asyncio

    from substrate.kernel.abstractions.core.identity import Actor
    from substrate.kernel.abstractions.runtime.store import RunSpec

    for _ in range(25):
        store = SqliteRuntimeStore(":memory:")
        await store.start()
        tasks = [asyncio.create_task(store.create_run(RunSpec(agent=Actor(type="agent", key=f"a{i}")))) for i in range(20)]
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.aclose()
