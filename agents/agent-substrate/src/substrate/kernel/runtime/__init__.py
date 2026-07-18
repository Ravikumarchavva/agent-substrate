"""kernel.runtime — durable runtime contracts (L0).

All items here are Protocols, dataclasses, or value types.
No I/O, no concrete implementations, no external dependencies beyond pydantic.

File map
--------
ids.py            RunId, RunStatus, new_run_id
log_entry.py      RunLogEntry, EventLogProtocol  (the append-only durable spine)
effects.py        Effect, EffectResult  (at-most-once external effects)
inbox.py          InboxProtocol, DeadLetterEntry, DeadLetterReason  (durable mailbox)
follow_graph.py   FollowGraph  (social follow-graph — NOT the RAG knowledge graph)
fanout.py         FanoutStrategy  (how an emit reaches all followers)
wakeup.py         Wakeup, SignalBusProtocol  (what resumes a dormant run)
scheduler.py      Lease, RunRetryPolicy, SchedulerProtocol  (work-queue + leasing)
supervisor.py     RunHandle, RunResult, SupervisorProtocol  (spawn/join/cancel subagents)
agent.py          AgentRunContext, Agent  (the agent contract)
communication.py  AskOutcome, RunStatusSummary  (ask/reply value types)
"""

from __future__ import annotations

from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.log_entry import EventLogProtocol, RunLogEntry
from substrate.kernel.runtime.effects import Effect, EffectResult
from substrate.kernel.runtime.inbox import (
    DeadLetterEntry,
    DeadLetterReason,
    InboxProtocol,
)
from substrate.kernel.runtime.follow_graph import FollowGraph
from substrate.kernel.runtime.fanout import FanoutStrategy
from substrate.kernel.runtime.wakeup import SignalBusProtocol, Wakeup
from substrate.kernel.runtime.scheduler import Lease, RunRetryPolicy, SchedulerProtocol
from substrate.kernel.runtime.supervisor import RunHandle, RunResult, SupervisorProtocol
from substrate.kernel.runtime.agent import Agent, AgentRunContext
from substrate.kernel.runtime.communication import AskOutcome, RunStatusSummary

__all__ = [
    # ids
    "RunId",
    "RunStatus",
    "new_run_id",
    # log
    "RunLogEntry",
    "EventLogProtocol",
    # effects
    "Effect",
    "EffectResult",
    # inbox
    "DeadLetterReason",
    "DeadLetterEntry",
    "InboxProtocol",
    # follow graph
    "FollowGraph",
    # fanout
    "FanoutStrategy",
    # wakeup
    "Wakeup",
    "SignalBusProtocol",
    # scheduler
    "RunRetryPolicy",
    "Lease",
    "SchedulerProtocol",
    # supervisor
    "RunHandle",
    "RunResult",
    "SupervisorProtocol",
    # agent
    "AgentRunContext",
    "Agent",
    # communication
    "AskOutcome",
    "RunStatusSummary",
]
