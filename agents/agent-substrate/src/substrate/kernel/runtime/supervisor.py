"""SupervisorProtocol — agents spawning, joining, and cancelling subagents.

This realizes supervision-v2 on the event-sourced substrate.

Relationship to kernel/agent/supervision.py
-------------------------------------------
``kernel/agent/supervision.py::Supervision`` is the **policy** half: tree position
(run_id, parent_id, root_id, depth), budget (SpawnBudget), and retention
(HistoryRetention).  ``SupervisorProtocol`` (this file) is the **runtime** half: the
contract that actually creates and joins running entities.

``Supervision.spawn_child()`` mints the child's policy object.
``SupervisorProtocol.spawn()`` takes that policy and makes a real durable run out of it.

The four hard properties realized on the durable substrate
----------------------------------------------------------
1. **Replay-deterministic spawn.**
   ``spawn`` appends ``child.spawned{child_run}`` to the *parent's* log before
   enqueuing the child.  On parent replay the journaled entry returns the *same*
   ``child_run_id`` — never a duplicate child.  Same idempotency-key mechanism
   as a tool call: look up by (parent_run_id, step_seq, "child.spawn", child_agent).

2. **Mobile children.**
   A child is its own run with its own EventLogProtocol — any worker can pick it up.
   ``join`` is a suspend point: the parent suspends with
   ``Wakeup(kind="child_done", child_run=handle.run_id)``.  When the child
   reaches a terminal state, its worker calls ``finish_run``, which marks the
   child terminal in the run tree and fires a ``child:{run_id}`` signal that
   wakes the parent through the SignalBusProtocol.

3. **Budget, not depth.**
   ``spawn`` consults a ``SpawnBudget`` bound to the root ``run_id``.  Over
   budget → ``BudgetExhaustedError`` (``kernel/exceptions.py`` — the same
   exception ``ExecutionTracker`` raises for token/cost/turn exhaustion; one
   exception type for "a budget of some kind ran out," not a spawn-specific
   one).  Per supervision-v2, ``max_agents`` / ``depth`` ceilings are
   dropped — the budget is the single constraint.  Enforced durably here
   (every ``SupervisorProtocol`` implementation), not only by the in-process
   ``SpawnTracker`` fast-path ``OrchestratorAgent`` uses.

4. **Cancellation cascade.**
   ``cancel(handle)`` durably marks ``handle``'s entire subtree (a recursive
   walk of the parent/child tree, not a wakeup message): live runs get
   ``cancel_requested`` set and observe it cooperatively at their next
   heartbeat (``ctx.check()`` then raises ``CancellationError``); suspended
   runs — with no live task left to ever heartbeat — are terminal-marked
   directly. Either way the at-most-once effect guarantee still holds; see
   ``Supervisor.cancel()`` for the concrete implementation.

Orphan handling on permanent parent failure (not yet implemented)
-------------------------------------------------------------------
Today ``cancel`` cascades to the whole subtree, and nothing else reacts to a
parent failing. The intended policy, keyed on each child's ``HistoryRetention``,
is: RUN → cascade-cancel, PERMANENT → detach and re-parent to the root,
NONE → cancel and compact the log; the disposition would be journaled in the
parent's terminal entry so it is replayable. Design it before relying on it.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from substrate.kernel.core.content import JsonObject
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import Message, Payload
from substrate.kernel.runtime.ids import RunId, RunStatus
from substrate.kernel.agent.supervision import Supervision


class RunHandle(BaseModel):
    """An opaque reference to a spawned run.

    Returned by ``SupervisorProtocol.spawn``; passed to ``join``/``cancel``/``ask``.
    ``parent_run`` links the child to the run that spawned it.

    ``boot_correlation_id`` is the (replay-stable) correlation id the child's
    boot message was actually delivered with. ``ctx.ask(handle, ...)`` uses
    it to wait for the child's reply WITHOUT re-delivering anything — the
    child was already started by ``spawn``'s own boot delivery, so a second
    send from ``ask`` would (a) be redundant and (b) collide with the InboxProtocol's
    idempotent-by-message-id dedup if the caller reuses the same ``Message``
    object for both calls (a natural, common pattern), silently dropping
    whichever delivery lands second.
    """

    run_id: RunId
    agent_id: Actor
    parent_run: RunId
    boot_correlation_id: str | None = None

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


class RunResult(BaseModel):
    """Terminal output of any run.

    Also the value returned by ``SupervisorProtocol.join`` and the result type for
    cross-agent delegation (send to an existing agent + await its completion).

    ``output`` carries the agent's final payload (if any).
    ``error`` carries the exception message on FAILED.
    ``metadata`` is a free-form dict for runtime diagnostics (timing, retries, etc.).
    """

    run_id: RunId
    status: RunStatus
    output: Payload | None = None
    error: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


@runtime_checkable
class SupervisorProtocol(Protocol):
    """Contract for agents to spawn, join, and cancel subagents.

    Implementations: in-process asyncio (Stage 0), Postgres-backed durable
    run registry (Stage 1), distributed with cross-region placement (Stage 3).

    Semantic guarantees
    -------------------
    - ``spawn`` is idempotent with respect to the parent's log: replaying the
      same parent log entry returns the same ``RunHandle`` (no duplicate child).
    - ``join`` suspends the *caller* (logs ``run.suspended`` on the parent run)
      and resumes it when the child reaches a terminal state.
    - ``cancel`` cascades recursively to the entire subtree rooted at ``handle``.
    - ``children_of`` reflects the current state of the child registry; it is
      used for crash reconciliation (parent folds its log, finds ``child.spawned``
      entries, and re-joins the still-live ones via this method).
    """

    async def spawn(
        self,
        child_agent: Actor,
        *,
        parent: RunId,
        supervision: Supervision,
        boot: Message,
        path: str,
        correlation_id: str,
    ) -> RunHandle:
        """Create and enqueue a new child run for ``child_agent``.

        ``supervision`` is the child's policy — caller should produce it via
        ``parent_supervision.spawn_child(parent_agent_id)``.

        ``boot`` is the first message delivered to the child's inbox to start
        its ``run()`` coroutine. Its ``id``/``correlation_id`` are typically
        freshly generated by the caller on every call and are NOT safe to use
        for the spawn effect's identity — see ``path``.

        ``path`` is the caller's own replay-stable identity for *this*
        ``ctx.spawn()`` call site (``RunContext._alloc_path()`` — "the Nth
        journaled call in this run"), used to derive the spawn effect's id.
        Implementations must NOT derive that id from anything computed fresh
        each call (e.g. the current ``last_seq`` of the parent's log, or
        ``boot.id``) — both are non-deterministic across a replay precisely
        because spawning is itself what advances them, which would make the
        effect id drift on every attempt and defeat the idempotency
        guarantee below.

        ``correlation_id`` (also caller-derived, replay-stable) is stamped
        onto the delivered boot message and returned on the ``RunHandle`` as
        ``boot_correlation_id`` — this is what ``ctx.ask(handle, ...)`` waits
        on, so the child's reply is found without a second delivery.

        Raises ``BudgetExhaustedError`` (``kernel/exceptions.py``) when the
        root's ``SpawnBudget`` is exhausted.

        The spawn is journaled on the parent before the child is enqueued —
        on parent replay the same child_run_id is returned deterministically.
        """
        ...

    async def join(self, handle: RunHandle) -> RunResult:
        """Return ``handle``'s run's terminal ``RunResult``, once available.

        The durable suspend-until-terminal guarantee this describes (the
        caller's run transitions to SUSPENDED, the SchedulerProtocol releases
        its lease, and the parent is re-enqueued with a ``child_done``
        wakeup when the child completes) is what ``ctx.join(handle)``
        achieves at the ``RunContext`` (L1) layer — via
        ``SignalBusProtocol``, not by calling this method directly. A
        backend's own ``join()`` is not required to block: a backend with
        no in-process way to await completion (e.g. a stateless Postgres
        client) MAY instead do a single point-in-time status read and is
        Protocol-conformant either way, since no caller in this codebase
        invokes ``SupervisorProtocol.join()`` directly outside that signal
        path today. Implementers that genuinely block until terminal (e.g.
        an in-process ``asyncio.Event``-backed backend) are also conformant
        — both are valid; check a specific implementation's own docstring
        for which one it does.
        """
        ...

    async def cancel(self, handle: RunHandle, *, reason: str) -> None:
        """Cancel ``handle``'s run and cascade to its entire subtree.

        Appends a cancel intent to the parent log and delivers cancel wakeups
        to all direct children recursively.  A child mid-effect honours the
        cancel at its next ``ctx.check()``; at-most-once effect guarantee holds.
        """
        ...

    def children_of(
        self,
        parent: RunId,
    ) -> AsyncIterator[RunHandle]:
        """Yield all ``RunHandle``s for runs spawned by ``parent``.

        Used for crash reconciliation: a resumed parent folds its log, finds
        its ``child.spawned`` entries, and calls this to re-join its children.
        Every child is yielded regardless of status — ``join`` returns the
        result of one that already finished.
        """
        ...

    async def finish_run(
        self, run_id: RunId, status: RunStatus, *, error: str | None = None
    ) -> None:
        """Record a run's terminal outcome and wake any waiting parent.

        Called by the Worker exactly once per run, on COMPLETED/FAILED/
        CANCELLED (never on SUSPENDED — a suspension is dormancy, not a
        terminal state). If ``run_id`` has a parent, this fires a
        ``child:{run_id}`` signal to it — the signal ``ctx.join``/``ctx.ask``
        consume to resume a suspended parent without polling.
        """
        ...

    async def supervision_of(self, run_id: RunId) -> Supervision | None:
        """Return the ``Supervision`` a spawned run was given at spawn time.

        ``None`` for a run never spawned via ``ctx.spawn()`` (a top-level
        ``submit()``) or on backends without persistence for it. The Worker
        calls this when leasing a run to populate ``RunMeta.supervision``,
        which is what lets ``ctx.spawn()`` inherit the caller's own
        ``execution_budget``/``spawn_budget`` (via
        ``Supervision.spawn_child()``) instead of always falling back to
        ``Supervision.root()`` — a fresh, unlimited budget with no relation
        to whatever constraints the calling run itself was given.
        """
        ...


__all__ = ["RunHandle", "RunResult", "SupervisorProtocol"]
