"""RuntimeStore — everything the durable runtime persists, behind one port.

The runtime used to persist through six protocols (event log, inbox, scheduler,
signal bus, supervisor, follow graph), each implemented three times — in memory,
SQLite and Postgres — with the *policy* duplicated in every copy. That is where
the drift came from: a heartbeat that cancelled every run after fifteen seconds
on one backend, a terminal transition spread over three stores that corrupted the
log when any step failed, a spawn that recorded its effect first and could strand
the parent forever.

The engine now owns all policy once. A store owns exactly one thing: **atomicity**.
Each method below is a single transaction. Anything that must happen together —
the journal entry, the queue status, the inbox acknowledgement, the signal that
wakes a parent — is one ``commit``, so there is no window in which half of it has
happened.

Fencing
-------
Every lease carries an ``epoch`` that increases each time the run is leased. A
command made under a lease whose epoch is no longer current raises
``LeaseLostError``. That is what stops a worker that was paused past its lease from
executing alongside the worker that replaced it.

Guarantees a store must give (each is a test in the conformance suite)
----------------------------------------------------------------------
* A ``commit`` is all-or-nothing, and is rejected whole under a stale epoch.
* A run has at most one terminal journal entry.
* A signal that arrives while a run is deciding to suspend is never lost: if the
  signal is already buffered when ``commit(Suspend)`` runs, the run does not sleep.
* A message id that has been acknowledged is never accepted again (dedup survives
  the ack, until pruned).
* Within a thread, at most one run is active at a time.
* Entries are appended in the order committed, ``seq`` contiguous from 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field

from substrate.kernel.abstractions.agent.supervision import Priority, Supervision
from substrate.kernel.abstractions.core.content import JsonObject, KernelModel
from substrate.kernel.abstractions.core.error_info import ErrorInfo
from substrate.kernel.abstractions.core.identity import Actor, Topic
from substrate.kernel.abstractions.core.trace import TraceContext
from substrate.kernel.abstractions.messaging.message import Message
from substrate.kernel.abstractions.runtime.ids import RunId, RunStatus
from substrate.kernel.abstractions.runtime.inbox import DeadLetterEntry
from substrate.kernel.abstractions.runtime.log_entry import RunLogEntry
from substrate.kernel.abstractions.runtime.scheduler import RunRetryPolicy
from substrate.kernel.abstractions.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.abstractions.runtime.wakeup import Wakeup

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class RunSpec(KernelModel):
    """What it takes to create a run."""

    agent: Actor
    tenant: str = "default"
    run_id: RunId | None = None
    thread_id: str | None = None
    parent_run_id: RunId | None = None
    priority: Priority = Priority.NORMAL
    retry_policy: RunRetryPolicy = Field(default_factory=RunRetryPolicy)
    deadline: datetime | None = None
    supervision: Supervision | None = None
    trace: TraceContext | None = None
    # Opaque to the engine: what a host needs to rebuild this run's agent after a restart.
    recipe: JsonObject | None = None


class RunRecord(KernelModel):
    """A run as the store knows it."""

    run_id: RunId
    agent: Actor
    tenant: str
    status: RunStatus
    thread_id: str | None = None
    parent_run_id: RunId | None = None
    tree_id: str = ""
    priority: Priority = Priority.NORMAL
    epoch: int = 0
    attempt: int = 0
    retry_count: int = 0
    retry_policy: RunRetryPolicy = Field(default_factory=RunRetryPolicy)
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    wake: Wakeup | None = None
    wake_at: datetime | None = None
    deadline: datetime | None = None
    cancel_requested: bool = False
    supervision: Supervision | None = None
    trace: TraceContext | None = None
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    terminated_at: datetime | None = None
    result: RunResult | None = None
    recipe: JsonObject | None = None


class Lease(KernelModel):
    """A worker's time-limited right to execute one run.

    ``epoch`` is the fence: it changes every time the run is leased, so a command
    carrying an old epoch is refused.
    """

    run_id: RunId
    agent: Actor
    worker_id: str
    epoch: int
    expires_at: datetime
    attempt: int = 1
    tenant: str = "default"
    thread_id: str | None = None
    retry_count: int = 0
    retry_policy: RunRetryPolicy = Field(default_factory=RunRetryPolicy)
    supervision: Supervision | None = None
    trace: TraceContext | None = None
    deadline: datetime | None = None
    started_at: datetime | None = None
    parent_run_id: RunId | None = None


class HeartbeatResult(StrEnum):
    """What a heartbeat found. Distinct on purpose: "healthy" and "stop" must not
    share a boolean, and a lost lease must not look like either."""

    OK = "ok"
    CANCEL_REQUESTED = "cancel_requested"
    DEADLINE = "deadline"
    LOST = "lost"


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


class Spend(KernelModel):
    """What a stretch of work cost: LLM tokens, dollars and round-trips."""

    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    turns: int = Field(default=0, ge=0)


class NewEntry(KernelModel):
    """A journal entry to append. The store assigns ``seq``.

    ``dedup_key`` makes the append idempotent: an entry whose key is already
    present in this run's log is silently skipped. That is what lets agent code
    write to the log from a body that replays — the entry lands once, however
    many times the line executes.

    ``ephemeral`` entries are live output (streamed tokens), not replay state: they
    are visible to a tail, never to a replay, and are removed when the run ends.

    ``spend`` is added to the run's execution tree's running total in the same transaction
    that writes the entry — and only if the entry is actually written, so a deduplicated repeat
    adds nothing and a replay can never count a call twice.
    """

    kind: str
    payload: JsonObject = Field(default_factory=dict)
    dedup_key: str | None = None
    ephemeral: bool = False
    spend: Spend | None = None


class Nack(KernelModel):
    """A message whose processing failed. It is retried until it has failed enough
    times, then dead-lettered; ``final`` dead-letters it now (the run that owned it
    is out of retries, so there is nobody left to try again)."""

    msg_id: str
    error: ErrorInfo
    final: bool = False


class SignalSpec(KernelModel):
    run_id: RunId
    name: str
    payload: JsonObject = Field(default_factory=dict)


class Delivery(KernelModel):
    agent: Actor
    msg: Message
    tenant: str = "default"


class SpawnSpec(KernelModel):
    """A child run to create atomically with the parent's journal entry.

    ``effect_id`` is the parent's replay-stable identity for this spawn: a replay
    that spawns again gets the same child back instead of a second one.
    """

    effect_id: str
    child: RunSpec
    boot: Message


class Suspend(KernelModel):
    kind: Literal["suspend"] = "suspend"
    wake: Wakeup


class Complete(KernelModel):
    kind: Literal["complete"] = "complete"
    output: JsonObject | None = None


class Fail(KernelModel):
    kind: Literal["fail"] = "fail"
    error: ErrorInfo


class Retry(KernelModel):
    """Fail this attempt and run again after ``delay_s`` (the engine computes the
    backoff; the store only parks the run until then)."""

    kind: Literal["retry"] = "retry"
    error: ErrorInfo
    delay_s: float = 0.0


class Cancel(KernelModel):
    kind: Literal["cancel"] = "cancel"
    reason: str = "cancelled"


Outcome = Annotated[Suspend | Complete | Fail | Retry | Cancel, Field(discriminator="kind")]


class Commit(KernelModel):
    """Everything that must happen together, as one transaction."""

    entries: tuple[NewEntry, ...] = ()
    ack: tuple[str, ...] = ()
    nack: tuple[Nack, ...] = ()
    deliveries: tuple[Delivery, ...] = ()
    signals: tuple[SignalSpec, ...] = ()
    spawns: tuple[SpawnSpec, ...] = ()
    outcome: Outcome | None = None


class DeliverResult(KernelModel):
    accepted: bool
    woke_run: RunId | None = None
    created_run: RunId | None = None


class CommitResult(KernelModel):
    seqs: tuple[int, ...] = ()
    spawned: dict[str, RunHandle] = Field(default_factory=dict)
    deliveries: tuple[DeliverResult, ...] = ()
    # The run asked to suspend but already had what it was waiting for, so it
    # was put straight back in the queue instead of going to sleep.
    resumed_immediately: bool = False
    # The run's status after the commit.
    status: RunStatus | None = None


class StoreStats(KernelModel):
    pending: int = 0
    running: int = 0
    suspended: int = 0
    dead_letters: int = 0
    oldest_lease_age_s: float = 0.0


# ---------------------------------------------------------------------------
# The port
# ---------------------------------------------------------------------------


@runtime_checkable
class RuntimeStore(Protocol):
    """The durable runtime's persistence. Every method is one transaction."""

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Open connections and apply the schema."""
        ...

    async def aclose(self) -> None: ...

    # -- runs ----------------------------------------------------------------

    async def create_run(
        self, spec: RunSpec, *, deliveries: Sequence[Delivery] = ()
    ) -> RunRecord:
        """Create a run in ``pending``, together with the messages it should find
        in its inbox, in one transaction.

        Raises ``ThreadBusyError`` — before delivering anything — if ``spec.thread_id``
        already has an active run.
        """
        ...

    async def lease(
        self, *, worker_id: str, capacity: int, lease_s: float, now: datetime
    ) -> list[Lease]:
        """Claim up to ``capacity`` runs for ``worker_id``.

        In one transaction: reclaim runs whose lease expired, wake suspended runs
        whose timer is due, terminate runs past their deadline, then claim by
        priority with fair-share across tenants, incrementing each run's epoch.
        """
        ...

    async def heartbeat(
        self, lease: Lease, *, lease_s: float, now: datetime
    ) -> HeartbeatResult:
        """Extend the lease, and report what the store knows about the run."""
        ...

    async def commit(self, lease: Lease, commit: Commit) -> CommitResult:
        """Apply ``commit`` atomically, fenced by ``lease.epoch``.

        Raises ``LeaseLostError`` (applying nothing) if the lease is no longer the
        run's current one.
        """
        ...

    async def get_run(self, run_id: RunId) -> RunRecord | None: ...

    async def find_runs(
        self,
        *,
        thread_id: str | None = None,
        agent: Actor | None = None,
        wake_signal: str | None = None,
        tenant: str | None = None,
        active_only: bool = True,
    ) -> list[RunRecord]:
        """Runs matching every given filter, oldest first. ``active_only`` limits
        to pending / running / suspended."""
        ...

    async def children(self, run_id: RunId) -> list[RunRecord]: ...

    async def request_cancel(self, run_id: RunId, *, reason: str, cascade: bool = True) -> list[RunId]:
        """Cancel a run and, if ``cascade``, everything it spawned.

        A pending or suspended run is cancelled on the spot (journal entry, parent
        signal and all); a running one is flagged and observed at its next
        heartbeat. Returns the runs affected.
        """
        ...

    # -- journal -------------------------------------------------------------

    async def read_events(
        self, run_id: RunId, *, from_seq: int = 0, limit: int | None = None, durable_only: bool = False
    ) -> list[RunLogEntry]: ...

    async def last_seq(self, run_id: RunId) -> int:
        """The highest ``seq`` in the run's log, or ``-1`` if it has none."""
        ...

    async def wait_events(self, run_id: RunId, *, after_seq: int, timeout_s: float) -> None:
        """Return once the run's log has an entry beyond ``after_seq``, or after
        ``timeout_s``. A hint to stop a tail polling; it may return early."""
        ...

    async def annotate(self, run_id: RunId, entries: Sequence[NewEntry]) -> list[int]:
        """Append durable entries to a run's record from outside its worker — a note a
        host adds to a thread (feedback, an MCP App's context). The store assigns ``seq``;
        no lease is needed, because these entries never take part in a run's replay.
        Returns the seqs. Raises ``KeyError`` for an unknown run."""
        ...

    async def append_ephemeral(self, lease: Lease, entries: Sequence[NewEntry]) -> None:
        """Append live (non-journal) entries under the lease. Fenced like a commit."""
        ...

    # -- inbox ---------------------------------------------------------------

    async def deliver(self, delivery: Delivery, *, wake: bool = True) -> DeliverResult:
        """Put a message in an agent's inbox.

        Rejected (``accepted=False``) if the id is pending *or already
        acknowledged*. With ``wake``: if the agent has a suspended run it goes
        back in the queue; if it has no active run, one is created — so a message
        is never left with nobody to read it.
        """
        ...

    async def drain(self, agent: Actor, *, limit: int = 100) -> list[Message]:
        """Pending messages, in per-sender order. Does not remove them; they stay
        until acknowledged by a ``commit``."""
        ...

    async def pending_count(self, agent: Actor) -> int: ...

    async def dead_letters(self, agent: Actor) -> list[DeadLetterEntry]: ...

    async def redrive(self, agent: Actor, msg_id: str) -> bool:
        """Move a dead letter back to the inbox. ``False`` if there is none."""
        ...

    # -- signals -------------------------------------------------------------

    async def signal(self, run_id: RunId, name: str, payload: JsonObject) -> bool:
        """Buffer a signal for a run; wake it if it is suspended waiting on
        ``name``. Returns whether it woke the run."""
        ...

    async def consume(self, run_id: RunId, name: str, claim_id: str) -> JsonObject | None:
        """Claim one buffered signal, exactly once per ``claim_id``: asking again
        with the same id returns the same payload."""
        ...

    # -- follow graph --------------------------------------------------------

    async def follow(self, follower: Actor, topic: Topic) -> None: ...

    async def unfollow(self, follower: Actor, topic: Topic) -> None: ...

    async def followers_of(self, topic: Topic) -> list[Actor]: ...

    # -- operations ----------------------------------------------------------

    async def stats(self) -> StoreStats: ...

    async def tree_spend(self, run_id: RunId) -> Spend:
        """Everything spent so far by the execution tree ``run_id`` belongs to — that run, its
        parent and siblings, and every descendant. What a tree-wide budget is checked against."""
        ...

    async def erase(self, *, tenant: str, thread_id: str | None = None) -> int:
        """Delete every run, log entry, message and signal belonging to a tenant
        (or one of its threads). Returns the number of runs removed."""
        ...

    async def prune(self, *, before: datetime) -> int:
        """Drop acknowledged-id records and terminal runs older than ``before``."""
        ...


__all__ = [
    "Cancel",
    "Commit",
    "CommitResult",
    "Complete",
    "DeliverResult",
    "Delivery",
    "Fail",
    "HeartbeatResult",
    "Lease",
    "Nack",
    "NewEntry",
    "Outcome",
    "Retry",
    "RunRecord",
    "RunSpec",
    "RuntimeStore",
    "SignalSpec",
    "SpawnSpec",
    "Spend",
    "StoreStats",
    "Suspend",
]
