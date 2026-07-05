"""agents.runtime.backends — Stage 0 in-memory implementations.

All backend classes implement the corresponding kernel Protocol
(EventLog, Inbox, Scheduler, etc.) using pure asyncio
data structures.  Stage 1 will add Postgres/Redis backends in
capabilities/runtime/ behind the same kernel contracts.
"""

from __future__ import annotations

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor

__all__ = [
    "InMemoryEventLog",
    "InMemoryInbox",
    "InMemoryFollowGraph",
    "PushAllFanout",
    "InMemorySignalBus",
    "InMemoryScheduler",
    "InMemorySupervisor",
]
