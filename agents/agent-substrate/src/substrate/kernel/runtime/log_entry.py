"""RunLogEntry and EventLogProtocol — the append-only durable spine of every run.

Named ``RunLogEntry`` (not ``RunEvent``) to avoid collision with
``integrations/events/envelope.py::EventEnvelope`` (the generic pub/sub
envelope — a different thing).

Truth model
-----------
A run's state is ``fold(entries from seq=0)`` — implemented by
``agents/runtime/effect_cache.py::EffectCache.fold()``, folded once per lease.
There is no separate checkpoint/snapshot type; nothing in this codebase
implements one today. If fold cost ever exceeds budget at P99, a log-
compaction snapshot would be the fix — evaluate then, not preemptively.

Optimistic concurrency
----------------------
``append()`` takes ``expected_seq`` — the caller's view of the current last
sequence number.  If the store's actual last_seq differs, it raises
``ConcurrentAppendError``.  This fences two workers from writing to the same
run simultaneously without a distributed lock.

Kinds
-----
``RunLogKind`` names the **core** kinds the runtime, replay and streaming depend
on. A kind belongs here when more than one module or layer emits or consumes it;
a kind private to one module (``ask.*``, ``join.completed``, ``hitl.question``,
``feed.curated``) stays a free string. Its values are the exact strings persisted in existing logs — never rename
one, or old runs stop replaying. ``RunLogEntry.kind`` stays a plain ``str`` on
purpose: application-level kinds (``ask.*``, ``subagent.*``, ``feed.curated`` …)
are free-form dotted-lowercase strings that do not belong in the kernel, and a
reader must still load a log containing a kind it has never seen.

Versioning
----------
``RunLogEntry.v`` is the schema version of the persisted entry. Bump it only for
a change old readers cannot handle; readers must keep accepting every version
they have ever written.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from substrate.kernel.core.content import JsonObject
from substrate.kernel.runtime.ids import RunId


class RunLogKind(StrEnum):
    """Core run-log kinds. Values are persisted — see the module docstring."""

    # Run lifecycle
    RUN_STARTED = "run.started"
    RUN_RESUMED = "run.resumed"
    RUN_SUSPENDED = "run.suspended"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    # Journaled effects (replayed from cache, never re-executed)
    EFFECT_RESULT = "effect.result"
    LLM_CALL = "llm.call"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"

    # Conversation and human-in-the-loop
    USER_MESSAGE = "user.message"
    USER_MESSAGE_FLAGGED = "user.message.flagged"  # marker referencing a user.message seq
    MCP_APP_CONTEXT = "mcp_app_context"  # legacy underscore spelling — persisted, keep
    INPUT_REQUESTED = "input.requested"
    APPROVAL_REQUESTED = "approval.requested"

    # Supervision
    CHILD_SPAWNED = "child.spawned"
    SUBAGENT_START = "subagent.start"
    SUBAGENT_DONE = "subagent.done"

    # Live streaming (not part of replay state)
    TEXT_DELTA = "text.delta"
    REASONING_DELTA = "reasoning.delta"


class RunLogEntry(BaseModel):
    """A single immutable entry in a run's event log.

    ``seq`` is monotonically increasing within a run, starting at 0.
    ``kind`` is a dot-namespaced string; core kinds are ``RunLogKind`` members.
    ``payload`` is a free-form JSON dict; callers type-narrow on ``kind``.
    ``v`` is the entry schema version (see module docstring).
    """

    run_id: RunId
    seq: int
    kind: str
    v: int = 1
    payload: JsonObject = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    model_config = {"frozen": True}


@runtime_checkable
class EventLogProtocol(Protocol):
    """Append-only, ordered log of ``RunLogEntry`` objects per run.

    Implementations: in-memory (Stage 0), Postgres append-only table with
    ``(run_id, seq)`` PK (Stage 1), NATS JetStream / Kafka (Stage 2+).

    Semantic guarantees all implementations must honour
    ---------------------------------------------------
    - Entries within a run are ordered by ``seq`` and never reordered.
    - ``append`` is atomic and serialised per run_id — no two appends to the
      same run succeed concurrently (optimistic-concurrency fencing via
      ``expected_seq``).
    - ``read`` is consistent: a reader sees entries in ``seq`` order with no gaps.
    - ``tail`` is a live view: after exhausting existing entries it waits for
      new ones indefinitely (until the caller cancels the iteration).
    """

    async def append(
        self,
        run_id: RunId,
        entry: RunLogEntry,
        *,
        expected_seq: int,
    ) -> int:
        """Append ``entry`` to run's log atomically.

        Returns the new sequence number assigned to the entry.

        Raises ``ConcurrentAppendError`` (from ``kernel/exceptions.py``) when
        the log's current ``last_seq`` differs from ``expected_seq`` — meaning
        another writer raced ahead.  Callers must reload and retry.
        """
        ...

    def read(
        self,
        run_id: RunId,
        *,
        from_seq: int = 0,
    ) -> AsyncIterator[RunLogEntry]:
        """Yield all entries for ``run_id`` starting at ``from_seq`` (inclusive).

        Completes when the log is exhausted (run reached a terminal state or
        the impl has no more buffered entries).  Use ``tail`` for live streaming.
        """
        ...

    def tail(
        self,
        run_id: RunId,
        *,
        from_seq: int = 0,
    ) -> AsyncIterator[RunLogEntry]:
        """Live-tail the log: yield existing entries then wait for new ones.

        Never completes on its own — cancel the enclosing async task to stop.
        Used by the Gateway for real-time "watch this agent" streaming and for
        VOD replay (``from_seq=0`` replays from the beginning).
        """
        ...

    async def last_seq(self, run_id: RunId) -> int:
        """Return the current last sequence number for ``run_id``.

        Returns ``-1`` when the log has no entries for that run yet
        (i.e. the run does not exist or has not written its first entry).
        """
        ...


__all__ = ["RunLogKind", "RunLogEntry", "EventLogProtocol"]
