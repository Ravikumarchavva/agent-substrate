"""agents.runtime.backends — Stage 0 (in-memory) and no-infra-durable
(SQLite) implementations.

Stage 0 (``InMemory*``): pure asyncio data structures, gone on restart.
No-infra-durable (``Local*``): the same kernel Protocols, backed by one
SQLite file — see ``_local_db.py``'s module docstring. The network-
distributed durable tier (Postgres) lives in ``infrastructure/runtime/``
instead, since it needs infra-level pool management ``local``'s zero-
dependency file doesn't.
"""

from __future__ import annotations

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor

from substrate.agents.runtime.backends._local_db import LocalRuntimeDB
from substrate.agents.runtime.backends._local_event_log import LocalEventLog
from substrate.agents.runtime.backends._local_follow_graph import LocalFollowGraph
from substrate.agents.runtime.backends._local_inbox import LocalInbox
from substrate.agents.runtime.backends._local_scheduler import LocalScheduler
from substrate.agents.runtime.backends._local_signal_bus import LocalSignalBus
from substrate.agents.runtime.backends._local_supervisor import LocalSupervisor

__all__ = [
    "InMemoryEventLog",
    "InMemoryInbox",
    "InMemoryFollowGraph",
    "PushAllFanout",
    "InMemorySignalBus",
    "InMemoryScheduler",
    "InMemorySupervisor",
    "LocalRuntimeDB",
    "LocalEventLog",
    "LocalFollowGraph",
    "LocalInbox",
    "LocalScheduler",
    "LocalSignalBus",
    "LocalSupervisor",
]
