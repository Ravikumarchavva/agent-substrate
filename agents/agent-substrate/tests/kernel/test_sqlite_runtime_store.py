"""The runtime store on the folder store, held to the shared conformance suite."""

from __future__ import annotations

import pytest

from substrate.testing.conformance.runtime_store import NOW, RuntimeStoreConformance
from substrate.testing.runtime import runtime_store


class TestRuntimeStoreOnSqlite(RuntimeStoreConformance):
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


async def test_a_conversation_is_followed_for_the_window_and_then_the_member_listens_for_its_name_again(tmp_path) -> None:
    from datetime import timedelta

    from substrate.runtime import Member, Mode
    from substrate.types import Actor

    now = [NOW]
    store = runtime_store(tmp_path / "store", clock=lambda: now[0])
    await store.start()
    scout, human, channel = Actor("agent", "scout@1"), Actor("user", "ravi"), "group/1"
    await store.channel_open(channel, members=[Member(agent=scout, mode=Mode.MENTIONS)], engage_s=60)
    await store.channel_append(channel, sender=human, text="@Scout hi", mentions=[str(scout)])

    now[0] = NOW + timedelta(seconds=30)
    assert scout in (await store.channel_append(channel, sender=human, text="and?")).woken  # still inside the window

    now[0] = NOW + timedelta(seconds=61)
    assert scout not in (await store.channel_append(channel, sender=human, text="hello?")).woken  # it has stopped following
    assert scout in (await store.channel_append(channel, sender=human, text="@Scout", mentions=[str(scout)])).woken
    await store.aclose()

