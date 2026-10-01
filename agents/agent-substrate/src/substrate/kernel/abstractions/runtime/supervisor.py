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
   exception the engine raises for token/cost/turn exhaustion; one
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

from pydantic import BaseModel, Field

from substrate.kernel.abstractions.core.content import JsonObject
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.message import Payload
from substrate.kernel.abstractions.runtime.ids import RunId, RunStatus


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


__all__ = ["RunHandle", "RunResult"]
