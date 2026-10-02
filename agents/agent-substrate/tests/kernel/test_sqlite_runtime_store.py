"""The runtime store on the folder store, held to the shared conformance suite."""

from __future__ import annotations

import pytest

from substrate.testing.conformance.runtime_store import NOW, RuntimeStoreConformance
from substrate.testing.runtime import runtime_store


class TestSqlRuntimeStoreOnSqlite(RuntimeStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = runtime_store(tmp_path / "store", clock=lambda: NOW)
        await store.start()
        yield store
        await store.aclose()


async def test_closing_while_cancelled_callers_statements_are_in_flight_does_not_crash() -> (
    None
):
    """A task cancelled mid-statement does not stop the statement: its thread finishes it.
    Closing the connection under that thread is a segfault, not an exception, so close
    must queue behind in-flight work. Repeated, because the window is narrow."""
    import asyncio

    from substrate.types import Actor
    from substrate.runtime import RunSpec

    for _ in range(25):
        store = runtime_store()
        await store.start()
        tasks = [
            asyncio.create_task(
                store.create_run(RunSpec(agent=Actor(type="agent", key=f"a{i}")))
            )
            for i in range(20)
        ]
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await store.aclose()
