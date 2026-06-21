"""RunLogEntry and EventLog — the append-only durable spine of every run.

Named ``RunLogEntry`` (not ``RunEvent``) to avoid collision with
``kernel/events.py::Event`` (the generic pub/sub envelope — a different thing).

Truth model
-----------
A run's state is ``fold(entries from seq=0)``.  ``Checkpoint`` (in
``kernel/agent.py``) is demoted to a log-compaction snapshot — an optimization
to bound rehydration cost, never the source of authority.

Optimistic concurrency
----------------------
``append()`` takes ``expected_seq`` — the caller's view of the current last
sequence number.  If the store's actual last_seq differs, it raises
``ConcurrentAppendError``.  This fences two workers from writing to the same
run simultaneously without a distributed lock.

Standard ``kind`` values
------------------------
``run.started``     — run opened; payload carries boot Message
``msg.received``    — message delivered to inbox and drained
``tool.called``     — tool invocation; payload carries Effect.id + spec
``tool.result``     — tool result journaled; payload carries EffectResult
``child.spawned``   — subagent spawned; payload carries child RunId + AgentId
``child.completed`` — child reached terminal state; payload carries RunResult ref
``run.suspended``   — run going dormant; payload carries Wakeup
``run.completed``   — terminal success; payload carries output
``run.failed``      — terminal failure; payload carries error message
``run.cancelled``   — terminal cancellation; payload carries reason + orphans_resolved
``orphans.resolved``— child disposition on permanent parent failure
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator, Protocol

from pydantic import BaseModel, Field

from substrate.kernel.core.content import JsonObject
from substrate.kernel.runtime.ids import RunId


class RunLogEntry(BaseModel):
    """A single immutable entry in a run's event log.

    ``seq`` is monotonically increasing within a run, starting at 0.
    ``kind`` is a dot-namespaced string — see module docstring for conventions.
    ``payload`` is a free-form JSON dict; callers type-narrow on ``kind``.
    """

    run_id: RunId
    seq: int
    kind: str
    payload: JsonObject = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    model_config = {"frozen": True}


class EventLog(Protocol):
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

        Raises ``ConcurrentAppendError`` (from ``kernel/errors.py``) when
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


__all__ = ["RunLogEntry", "EventLog"]
