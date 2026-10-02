"""Invariant register — a run is replayed only by the code that started it (row I15).

A run's journal records what one version of an agent did. Handing that record to a
different version attaches old results to changed code — a renamed tool, a reordered
call — which is how a replay quietly goes wrong. The run's own journal pins the version
that started it, however the run was created, and a worker with a different one refuses
to continue it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from substrate.types import Actor
from substrate.runtime import DataPayload, Message
from substrate.types import RunLogKind
from substrate.runtime import Runtime

TERMINAL = (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED, RunLogKind.RUN_CANCELLED)


class Waiter:
    """Suspends on a signal, so the run is mid-flight when the 'deploy' happens."""

    def __init__(self, version: str) -> None:
        self.id = Actor("agent", "waiter")
        self.version = version
        self.resumed = False

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        await ctx.sleep_until_signal("go")
        self.resumed = True


async def _terminal(rt: Runtime, run_id: str) -> tuple[str, dict[str, Any]]:
    async def watch() -> tuple[str, dict[str, Any]]:
        async for entry in rt.tail(run_id):
            if entry.kind in TERMINAL:
                return str(entry.kind), dict(entry.payload or {})
        raise AssertionError("tail ended without a terminal entry")

    return await asyncio.wait_for(watch(), 10)


async def _suspend_under(path: Path, version: str) -> str:
    agent = Waiter(version)
    async with Runtime.local(path) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, Message(target=agent.id, sender=Actor.system("t"), payload=DataPayload(data={})))
        for _ in range(300):
            if (await rt.get_run(run_id)).status == "suspended":
                return str(run_id)
            await asyncio.sleep(0.01)
    raise AssertionError("the run never suspended")


async def test_i15_a_run_continues_under_the_version_that_started_it(tmp_path: Path) -> None:
    path = tmp_path / "rt.sqlite3"
    run_id = await _suspend_under(path, "1")

    same = Waiter("1")
    async with Runtime.local(path) as rt:
        await rt.register(same)
        await rt.store.signal(run_id, "go", {})
        kind, _ = await _terminal(rt, run_id)

    assert kind == RunLogKind.RUN_COMPLETED and same.resumed


async def test_i15_a_run_is_refused_by_a_different_version(tmp_path: Path) -> None:
    path = tmp_path / "rt.sqlite3"
    run_id = await _suspend_under(path, "1")

    changed = Waiter("2")
    async with Runtime.local(path) as rt:
        await rt.register(changed)
        await rt.store.signal(run_id, "go", {})
        kind, payload = await _terminal(rt, run_id)

    assert kind == RunLogKind.RUN_FAILED and not changed.resumed, "the new code must not continue the old run"
    assert "version" in str(payload.get("error", "")), payload
