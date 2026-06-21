"""Runtime errors — single source of truth for agent execution failures."""

from __future__ import annotations

from substrate.kernel.core.identity import AgentId


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

    The orchestrator catches this, consults the retry policy, and
    re-dispatches the agent via the dispatch tool, resuming from the
    last ``Checkpoint`` (see ``kernel/agent.py``).

    ``run_id`` and ``agent_id`` identify which run/agent failed so the
    orchestrator knows which history and checkpoint to reload.
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

    Raise from any ``AgentMiddleware``, ``ChatMiddleware``, or
    ``FunctionMiddleware`` to stop execution at that level.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CancellationError(KernelError):
    """Raised when an operation is cancelled via ``CancellationToken``.

    Agents and tools should propagate this rather than catch and swallow it,
    so the cancellation can reach the outermost caller cleanly.
    """


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


__all__ = [
    "KernelError",
    "AgentNotFoundError",
    "HandlerError",
    "AgentCrashError",
    "BudgetExhaustedError",
    "MiddlewareTermination",
    "CancellationError",
    "ConcurrentAppendError",
    "SpawnDenied",
]
