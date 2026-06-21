"""agents.runtime.backends — Stage 0 in-memory implementations.

All backend classes implement the corresponding kernel Protocol
(EventLog, Journal, Inbox, Scheduler, etc.) using pure asyncio
data structures.  Stage 1 will add Postgres/Redis backends in
capabilities/runtime/ behind the same kernel contracts.
"""

from __future__ import annotations

from agent_substrate.agents.runtime.backends._event_log import InMemoryEventLog
from agent_substrate.agents.runtime.backends._fanout import PushAllFanout
from agent_substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from agent_substrate.agents.runtime.backends._inbox import InMemoryInbox
from agent_substrate.agents.runtime.backends._journal import InMemoryJournal
from agent_substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from agent_substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from agent_substrate.agents.runtime.backends._supervisor import InMemorySupervisor

__all__ = [
    "InMemoryEventLog",
    "InMemoryJournal",
    "InMemoryInbox",
    "InMemoryFollowGraph",
    "PushAllFanout",
    "InMemorySignalBus",
    "InMemoryScheduler",
    "InMemorySupervisor",
]
