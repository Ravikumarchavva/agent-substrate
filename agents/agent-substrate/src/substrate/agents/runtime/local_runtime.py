"""build_local_runtime() — Runtime backed by the SQLite no-infra durable tier.

Usage::

    from substrate.agents.runtime.local_runtime import build_local_runtime

    async with build_local_runtime(path="./data/db/runtime.sqlite3") as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, msg)

The one tier that needs neither Docker nor a network service: "a folder and
everything dumps there." Durable across restarts, unlike the default
in-memory ``Runtime()``; single-host (not network-distributed), unlike
``build_postgres_runtime()`` — see ``runtime/backends/_local_db.py``'s
module docstring for the full tradeoff.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._local_db import LocalRuntimeDB
from substrate.agents.runtime.backends._local_event_log import LocalEventLog
from substrate.agents.runtime.backends._local_follow_graph import LocalFollowGraph
from substrate.agents.runtime.backends._local_inbox import LocalInbox
from substrate.agents.runtime.backends._local_scheduler import LocalScheduler
from substrate.agents.runtime.backends._local_signal_bus import LocalSignalBus
from substrate.agents.runtime.backends._local_supervisor import LocalSupervisor
from substrate.agents.runtime.runtime import Runtime


@asynccontextmanager
async def build_local_runtime(
    *, path: str | Path = "./data/db/runtime.sqlite3"
) -> AsyncIterator[Runtime]:
    """Create a Runtime backed by one SQLite file at *path*.

    The file (and its parent directory) is created on first use. Closes the
    underlying connection on exit.
    """
    db = LocalRuntimeDB(path)
    try:
        event_log = LocalEventLog(db)
        inbox = LocalInbox(db)
        scheduler = LocalScheduler(db)
        signal_bus = LocalSignalBus(db, scheduler)
        follow_graph = LocalFollowGraph(db)
        supervisor = LocalSupervisor(
            db,
            event_log=event_log,
            inbox=inbox,
            scheduler=scheduler,
            signal_bus=signal_bus,
        )

        async with Runtime(
            event_log=event_log,
            inbox=inbox,
            scheduler=scheduler,
            signal_bus=signal_bus,
            supervisor=supervisor,
            follow_graph=follow_graph,
            fanout=PushAllFanout(),
        ) as rt:
            yield rt
    finally:
        db.close()


__all__ = ["build_local_runtime"]
