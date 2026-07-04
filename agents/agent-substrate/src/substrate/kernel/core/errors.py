"""Runtime errors — single source of truth for agent execution failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.kernel.core.identity import AgentId

if TYPE_CHECKING:
    from substrate.kernel.runtime.wakeup import Wakeup


class KernelError(Exception):
    """Base class for all ravi kernel errors.

    Catching ``KernelError`` is sufficient to intercept any typed error
    raised by the runtime, routing, or budget layers.
    """


class AgentNotFoundError(KernelError):
    """Raised when sending to an AgentId that has no registered handler."""

    def __init__(self, message: str, *, agent_id: AgentId | None = None) -> None:
        super().__init__(message)
        self.agent_id = agent_id


class HandlerError(KernelError):
    """Raised when a message handler raises an exception.

    Wraps the original exception so callers receive a typed error rather
    than a bare exception or silent ``None``.
    """

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class AgentCrashError(KernelError):
    """Raised when an agent's run fails with an unexpected exception.

    The Worker catches this, journals ``run.failed``, and (per retry policy)
    re-enqueues the run. Resume is a fresh lease: any worker folds a new
    ``EffectCache`` from the EventLog (``fold(entries from seq=0)`` — see
    ``kernel/runtime/log_entry.py``) and calls ``agent.run()`` again from the
    top; every already-completed effect replays as a cache hit. There is no
    separate checkpoint/snapshot mechanism — the EventLog fold is the sole
    source of truth.

    ``run_id`` and ``agent_id`` identify which run/agent failed so the
    resuming worker knows which EventLog to fold.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        agent_id: AgentId,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.agent_id = agent_id


class BudgetExhaustedError(KernelError):
    """Raised when an agent headcount or token/cost/turn budget is exhausted.

    Prevents runaway trees where many levels each spawn many children,
    multiplying to thousands of agents (and LLM calls) in one run.
    """


class MiddlewareTermination(KernelError):
    """Raised by any middleware to immediately halt the agent run.

    Unlike ``AgentCrashError`` (unexpected failure), ``MiddlewareTermination``
    is an intentional policy-enforced halt — a guardrail blocked the request,
    the rate limit was exceeded, etc.  The agent loop catches it and produces
    an ``AgentRunResult`` with ``status="guardrail_tripped"``.

    Raise from any middleware (see ``substrate.kernel.agent.middleware``)
    to stop execution at whichever stage it's running in.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CancellationError(KernelError):
    """Raised when an operation is cancelled via ``CancellationToken``.

    Agents and tools should propagate this rather than catch and swallow it,
    so the cancellation can reach the outermost caller cleanly.
    """


class SuspendInterrupt(BaseException):
    """Raised to unwind a run to the Worker when it must go dormant.

    Deliberately a ``BaseException``, not ``Exception``: agent and tool code
    routinely wraps journaled calls in broad ``except Exception`` blocks (to
    record a journal error and re-raise). If this were an ``Exception``, that
    kind of handler would silently swallow the suspend signal and journal it
    as a failed effect instead of letting it unwind to the Worker.

    ``wakeup`` (a ``kernel.runtime.wakeup.Wakeup``, referenced under
    ``TYPE_CHECKING`` to avoid a kernel/core -> kernel/runtime import cycle)
    is what the Worker passes to ``Scheduler.release(status=SUSPENDED,
    wake_on=wakeup)`` — it's how the raiser (``RunContext``) tells the
    catcher (``Worker``) what should wake this run back up.
    """

    def __init__(self, run_id: str, wakeup: "Wakeup", *, reason: str = "") -> None:
        super().__init__(f"run {run_id} suspended" + (f": {reason}" if reason else ""))
        self.run_id = run_id
        self.wakeup = wakeup
        self.reason = reason


class ConcurrentAppendError(KernelError):
    """Raised by ``EventLog.append`` when optimistic concurrency fails.

    Two workers tried to write to the same run simultaneously.  The caller
    must reload the current ``last_seq`` and retry with the correct value.

    ``run_id``       — the run whose log had a concurrent write.
    ``expected_seq`` — the seq the caller assumed was current.
    ``actual_seq``   — the seq the store actually has.
    """

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        expected_seq: int,
        actual_seq: int,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.expected_seq = expected_seq
        self.actual_seq = actual_seq


class SpawnDenied(KernelError):
    """Raised by ``Supervisor.spawn`` when the root run's SpawnBudget is exhausted.

    Analogous to a denied tool approval — the agent author should handle this
    explicitly (e.g. retry later, degrade gracefully, or surface to the user).

    ``parent_run`` — the run that attempted the spawn.
    ``budget``     — the budget ceiling that was hit.
    """

    def __init__(
        self,
        message: str,
        *,
        parent_run: str,
        budget: int,
    ) -> None:
        super().__init__(message)
        self.parent_run = parent_run
        self.budget = budget


class ThreadBusyError(KernelError):
    """Raised by ``Scheduler.enqueue`` when ``thread_id`` already has an
    active (PENDING/RUNNING/SUSPENDED) run.

    Durable, cross-replica single-flight: unlike a per-process
    ``asyncio.Lock``, this is enforced by the backing store itself (a unique
    partial index on the durable backend), so a second replica racing to
    start a run for the same thread gets the same rejection a same-process
    caller would. Serving code should translate this into an HTTP 409.
    """

    def __init__(self, message: str, *, thread_id: str) -> None:
        super().__init__(message)
        self.thread_id = thread_id


__all__ = [
    "KernelError",
    "AgentNotFoundError",
    "HandlerError",
    "AgentCrashError",
    "BudgetExhaustedError",
    "MiddlewareTermination",
    "CancellationError",
    "SuspendInterrupt",
    "ConcurrentAppendError",
    "SpawnDenied",
    "ThreadBusyError",
]
