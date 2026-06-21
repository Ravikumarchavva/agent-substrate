"""kernel.runtime — durable runtime contracts (L0).

All items here are Protocols, dataclasses, or value types.
No I/O, no concrete implementations, no external dependencies beyond pydantic.

File map
--------
ids.py            RunId, RunStatus, new_run_id
log_entry.py      RunLogEntry, EventLog  (the append-only durable spine)
effects.py        Effect, EffectResult, Journal  (at-most-once external effects)
inbox.py          Inbox, DeadLetterEntry, DeadLetterReason  (durable mailbox)
follow_graph.py   FollowGraph  (social follow-graph — NOT the RAG knowledge graph)
fanout.py         FanoutStrategy  (how an emit reaches all followers)
wakeup.py         Wakeup, SignalBus  (what resumes a dormant run)
scheduler.py      Lease, RunRetryPolicy, Scheduler  (work-queue + leasing)
supervisor.py     RunHandle, RunResult, Supervisor  (spawn/join/cancel subagents)
agent.py          AgentRunContext, Agent  (the agent contract)
communication.py  AskOutcome, RunStatusSummary  (ask/reply value types)
"""

from __future__ import annotations

from agent_substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from agent_substrate.kernel.runtime.log_entry import EventLog, RunLogEntry
from agent_substrate.kernel.runtime.effects import Effect, EffectResult, Journal
from agent_substrate.kernel.runtime.inbox import DeadLetterEntry, DeadLetterReason, Inbox
from agent_substrate.kernel.runtime.follow_graph import FollowGraph
from agent_substrate.kernel.runtime.fanout import FanoutStrategy
from agent_substrate.kernel.runtime.wakeup import SignalBus, Wakeup
from agent_substrate.kernel.runtime.scheduler import Lease, RunRetryPolicy, Scheduler
from agent_substrate.kernel.runtime.supervisor import RunHandle, RunResult, Supervisor
from agent_substrate.kernel.runtime.agent import Agent, AgentRunContext
from agent_substrate.kernel.runtime.communication import AskOutcome, RunStatusSummary

__all__ = [
    # ids
    "RunId",
    "RunStatus",
    "new_run_id",
    # log
    "RunLogEntry",
    "EventLog",
    # effects
    "Effect",
    "EffectResult",
    "Journal",
    # inbox
    "DeadLetterReason",
    "DeadLetterEntry",
    "Inbox",
    # follow graph
    "FollowGraph",
    # fanout
    "FanoutStrategy",
    # wakeup
    "Wakeup",
    "SignalBus",
    # scheduler
    "RunRetryPolicy",
    "Lease",
    "Scheduler",
    # supervisor
    "RunHandle",
    "RunResult",
    "Supervisor",
    # agent
    "AgentRunContext",
    "Agent",
    # communication
    "AskOutcome",
    "RunStatusSummary",
]
