"""Invariant register — a healthy run is never mistaken for a dead one (row I13).

A lease is a time-limited right to run. A worker that is alive keeps it alive with
heartbeats; only a worker that stopped heartbeating loses it. The failure this row
guards is the inverse: a heartbeat that reported "stop" to a healthy run, or a lease that
lapsed under work that was still going, cancelled or duplicated every run longer than the
lease.
"""

from __future__ import annotations

import asyncio
from typing import Any

from substrate.types import Actor
from substrate.runtime import DataPayload, Message
from substrate.types import RunLogKind
from substrate.testing.runtime import ephemeral_runtime


class Slow:
    def __init__(self, seconds: float) -> None:
        self.id = Actor("agent", "slow")
        self.seconds = seconds
        self.executions = 0

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        self.executions += 1
        await asyncio.sleep(self.seconds)


async def test_i13_a_run_longer_than_its_lease_completes_once() -> None:
    agent = Slow(seconds=1.5)
    async with ephemeral_runtime(lease_s=0.4, poll_interval_s=0.02) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, Message(target=agent.id, sender=Actor.system("t"), payload=DataPayload(data={})), max_retries=0)

        async def terminal() -> str:
            async for entry in rt.tail(run_id):
                if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED, RunLogKind.RUN_CANCELLED):
                    return str(entry.kind)
            raise AssertionError("no terminal entry")

        assert await asyncio.wait_for(terminal(), 15) == RunLogKind.RUN_COMPLETED
        assert agent.executions == 1, "the lease lapsed under a healthy run and it was started again"
