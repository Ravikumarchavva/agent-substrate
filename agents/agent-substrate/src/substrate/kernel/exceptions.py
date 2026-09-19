"""Kernel exceptions — single source of truth for L0 execution failures and control signals.

Architecture:
  BaseException
  └── ControlSignal (never caught by 'except Exception:', unwinds worker directly)
      ├── SuspendInterrupt (agent goes dormant, awaits wakeup signal)
      └── CancellationError (run cancelled via CancellationToken)

  KernelError (root of all runtime execution errors)
  ├── TransientError (scheduler retries with exponential backoff)
  │   ├── ConcurrentAppendError (optimistic concurrency conflict on EventLog)
  │   └── ThreadBusyError (thread currently has an active run)
  ├── PermanentError (scheduler fails immediately on attempt 1 — no retry)
  │   ├── AgentCrashError (unhandled exception inside agent code)
  │   └── BlockValidationError (content block failed schema validation)
  └── PolicyTermination (intentional governance and safety halt)
      ├── BudgetExhaustedError (token, cost, turn, or headcount budget exceeded)
      └── MiddlewareTermination (guardrail tripped, rate limit reached)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.kernel.core.identity import Actor

if TYPE_CHECKING:
    from substrate.kernel.runtime.wakeup import Wakeup


# ---------------------------------------------------------------------------
# Control Signals (BaseException — Unwinds Worker, Never Swallowed)
# ---------------------------------------------------------------------------


class ControlSignal(BaseException):
    """Base class for runtime control signals that must unwind to the Worker.

    Inherits from ``BaseException`` rather than ``Exception`` so that general
    ``except Exception:`` handlers in user tools or middleware cannot swallow
    lifecycle signals like suspension or cancellation.
    """


class SuspendInterrupt(ControlSignal):
    """Raised to unwind a run to the Worker when it must go dormant.

    The raiser (``RunContext``) supplies a ``wakeup`` payload instructing
    the scheduler what event should wake this run back up.
    """

    def __init__(self, run_id: str, wakeup: "Wakeup", *, reason: str = "") -> None:
        super().__init__(f"run {run_id} suspended" + (f": {reason}" if reason else ""))
        self.run_id = run_id
        self.wakeup = wakeup
        self.reason = reason


class CancellationError(ControlSignal):
    """Raised when an operation is cancelled via ``CancellationToken``."""


# ---------------------------------------------------------------------------
# Root Kernel Error
# ---------------------------------------------------------------------------


class KernelError(Exception):
    """Base class for all substrate kernel execution errors.

    Catching ``KernelError`` is sufficient to intercept any typed error
    raised by the runtime, routing, or budget layers.
    """


# ---------------------------------------------------------------------------
# Transient Errors (Retryable with Backoff)
# ---------------------------------------------------------------------------


class TransientError(KernelError):
    """Base for errors that the scheduler can safely retry with exponential backoff.

    Indicates an environmental or concurrency conflict that may succeed on a subsequent attempt.
    """


class ConcurrentAppendError(TransientError):
    """Raised by ``EventLogProtocol.append`` when optimistic concurrency fails.

    Two workers tried to write to the same run simultaneously. The caller
    must reload the current ``last_seq`` and retry with the correct value.
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


class ThreadBusyError(TransientError):
    """Raised by ``SchedulerProtocol.enqueue`` when ``thread_id`` already has an active run."""

    def __init__(self, message: str, *, thread_id: str) -> None:
        super().__init__(message)
        self.thread_id = thread_id


# ---------------------------------------------------------------------------
# Permanent Errors (Fatal — Fail Immediately)
# ---------------------------------------------------------------------------


class PermanentError(KernelError):
    """Base for failures that are never worth retrying.

    The Worker's failure handler skips retry policies entirely and terminal-fails
    the run on the first attempt (e.g. invalid schemas, fatal bugs, corrupt state).
    """


class AgentCrashError(PermanentError):
    """Raised when an agent's run fails with an unexpected exception."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        agent_id: Actor,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.agent_id = agent_id

    def __str__(self) -> str:
        return f"[{self.agent_id} in run {self.run_id}] {super().__str__()}"


class BlockValidationError(PermanentError, ValueError):
    """Raised when a content block fails schema validation.

    Inherits from both ``PermanentError`` and ``ValueError`` for compatibility
    with standard Python data validation.
    """


# ---------------------------------------------------------------------------
# Policy Terminations (Intentional Governance & Safety Halts)
# ---------------------------------------------------------------------------


class PolicyTermination(KernelError):
    """Base for intentional, policy-enforced halts (not bugs or crashes)."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class BudgetExhaustedError(PolicyTermination):
    """Raised when an agent headcount or token/cost/turn budget is exhausted."""


class MiddlewareTermination(PolicyTermination):
    """Raised by any middleware to immediately halt the agent run (e.g. guardrail)."""

class BranchHeadConflictError(TransientError):
    """Raised when set_branch_head or append_and_advance fails optimistic concurrency."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str,
        branch_id: str,
        expected: str | int | None,
        actual: str | int | None,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.branch_id = branch_id
        self.expected = expected
        self.actual = actual


class SnapshotConflictError(TransientError):
    """Raised when commit_snapshot fails optimistic concurrency check."""

    def __init__(
        self,
        message: str,
        *,
        session_id: str,
        branch_id: str,
        expected_parent_id: str | None,
        actual_parent_id: str | None,
    ) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.branch_id = branch_id
        self.expected_parent_id = expected_parent_id
        self.actual_parent_id = actual_parent_id


class BranchNotFoundError(PermanentError, KeyError):
    """Raised when a requested branch_id does not exist in the session."""


class BranchAlreadyExistsError(PermanentError, ValueError):
    """Raised when attempting to create or fork to a branch_id that already exists."""


class DAGIntegrityError(PermanentError, ValueError):
    """Raised on cross-session edges, self-loops, invalid parents, or DAG corruption."""


__all__ = [
    # Control Signals
    "ControlSignal",
    "SuspendInterrupt",
    "CancellationError",
    # Root Error
    "KernelError",
    # Transient / Retryable
    "TransientError",
    "ConcurrentAppendError",
    "ThreadBusyError",
    "BranchHeadConflictError",
    "SnapshotConflictError",
    # Permanent / Fatal
    "PermanentError",
    "AgentCrashError",
    "BlockValidationError",
    "BranchNotFoundError",
    "BranchAlreadyExistsError",
    "DAGIntegrityError",
    # Governance / Policy
    "PolicyTermination",
    "BudgetExhaustedError",
    "MiddlewareTermination",
]
