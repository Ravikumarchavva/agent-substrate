"""The agent engine's typed failures and control signals, on top of Loamz's one error hierarchy.

::

    BaseException
    └── ControlSignal              unwinds the worker; never swallowed by `except Exception`
        ├── SuspendInterrupt       the run goes dormant until a wakeup
        ├── CancellationError      the run was cancelled, or its deadline passed
        └── LeaseLostError         another worker now owns this run; stop touching it

    loamz.engine.errors.LoamzError      `code` (stable) and `retryable`
    ├── TransientError
    │   └── ConflictError
    │       ├── ConcurrentAppendError
    │       ├── ThreadBusyError
    │       ├── BranchHeadConflictError
    │       └── SnapshotConflictError
    ├── PermanentError
    │   ├── AgentCrashError
    │   ├── ToolDeclarationError / UnroutableMessageError
    │   ├── InvalidRequestError
    │   │   ├── BlockValidationError / UnsupportedContentError
    │   │   ├── BranchAlreadyExistsError / DAGIntegrityError
    │   │   └── WorkspacePathError
    │   ├── NotFoundError
    │   │   └── BranchNotFoundError
    │   ├── ContextLengthError     the prompt exceeded the model's window
    │   ├── ContentFilterError     the provider refused on content grounds
    │   ├── AuthError              the model provider rejected our credentials
    │   ├── NonDeterminismError    a replay diverged from its journal
    │   └── OrphanedEffectError    an effect started and its outcome was never recorded
    └── PolicyTermination          intentional halt, not a bug
        ├── BudgetExhaustedError
        └── MiddlewareTermination

The shared classes (`TransientError`, `PermanentError`, `RateLimitedError`, `ServiceUnavailableError`,
`NotFoundError`, `ScopeViolationError`, `VectorSpaceError`, `QuotaExceededError`, …) are imported from
`loamz.engine.errors`; this module adds only what is particular to running agents. Every exception can be
pickled and copied (a failure may cross a process boundary).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loamz.engine.errors import (
    ConflictError,
    InvalidRequestError,
    LoamzError,
    NotFoundError,
    PermanentError,
)

if TYPE_CHECKING:
    from loamz.engine.agents.kernel.types.identity import Actor
    from loamz.engine.agents.kernel.types.wakeup import Wakeup


def _rebuild(cls: type, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return cls(*args, **kwargs)


# ── control signals ─────────────────────────────────────────────────────────


class ControlSignal(BaseException):
    """Runtime control flow that must unwind to the worker.

    A ``BaseException`` so that a broad ``except Exception`` in agent or tool code cannot swallow suspension or
    cancellation."""

    _ctor: tuple[tuple[Any, ...], dict[str, Any]]

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        instance = super().__new__(cls, *args)
        instance._ctor = (args, kwargs)
        return instance

    def __reduce__(self) -> tuple[Any, ...]:
        args, kwargs = self._ctor
        return (_rebuild, (type(self), args, kwargs))


class SuspendInterrupt(ControlSignal):
    """Unwinds a run to the worker so it can go dormant until ``wakeup``."""

    def __init__(self, run_id: str, wakeup: Wakeup, *, reason: str = "") -> None:
        super().__init__(f"run {run_id} suspended" + (f": {reason}" if reason else ""))
        self.run_id = run_id
        self.wakeup = wakeup
        self.reason = reason


class CancellationError(ControlSignal):
    """The run was cancelled, or its deadline passed."""


class LeaseLostError(ControlSignal):
    """This worker no longer owns the run: its lease expired and another worker holds it. Continuing would let two
    workers execute the same run."""

    def __init__(self, run_id: str, *, epoch: int | None = None) -> None:
        super().__init__(f"lease on run {run_id} was lost")
        self.run_id = run_id
        self.epoch = epoch


# ── conflicts (retrying can help) ───────────────────────────────────────────


class ConcurrentAppendError(ConflictError):
    """The journal had moved on: another writer won."""

    def __init__(self, message: str, *, run_id: str, expected_seq: int, actual_seq: int) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.expected_seq = expected_seq
        self.actual_seq = actual_seq


class ThreadBusyError(ConflictError):
    """The thread already has an active run."""

    def __init__(self, message: str, *, thread_id: str) -> None:
        super().__init__(message)
        self.thread_id = thread_id


class BranchHeadConflictError(ConflictError):
    """A branch-head update lost an optimistic-concurrency check."""

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


class SnapshotConflictError(ConflictError):
    """A workspace snapshot commit lost an optimistic-concurrency check."""

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


# ── permanent failures ──────────────────────────────────────────────────────


class AgentCrashError(PermanentError):
    """An agent's run failed with an unexpected exception."""

    def __init__(self, message: str, *, run_id: str, agent_id: Actor) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.agent_id = agent_id

    def __str__(self) -> str:
        return f"[{self.agent_id} in run {self.run_id}] {super().__str__()}"


class ToolDeclarationError(PermanentError, ValueError):
    """A tool did not say how dangerous it is or whether it is safe to run twice.

    Both facts decide what the engine does (whether to ask a human first, whether a call that may or may not have
    happened can be repeated), so a tool that leaves them out is refused when it is registered."""

    def __init__(self, tool: str, problems: tuple[str, ...]) -> None:
        super().__init__(f"tool {tool!r} is not fit to register: " + "; ".join(problems))
        self.tool = tool
        self.problems = problems


class UnroutableMessageError(PermanentError):
    """A message arrived that no handler of the receiving agent accepts. Redelivering it cannot change that."""

    def __init__(
        self, message: str, *, agent: str, payload_type: str, accepts: tuple[str, ...]
    ) -> None:
        super().__init__(message)
        self.agent = agent
        self.payload_type = payload_type
        self.accepts = accepts


class BlockValidationError(InvalidRequestError, ValueError):
    """A content block failed validation."""


class UnsupportedContentError(InvalidRequestError, ValueError):
    """A client cannot process this mix of content."""


class BranchNotFoundError(NotFoundError, KeyError):
    """The requested branch does not exist."""

    def __str__(self) -> str:  # KeyError would quote the message
        return str(self.args[0]) if self.args else ""


class BranchAlreadyExistsError(InvalidRequestError, ValueError):
    """The branch id is already taken."""


class DAGIntegrityError(InvalidRequestError, ValueError):
    """A cross-session edge, self-loop, invalid parent or other DAG corruption."""


class WorkspacePathError(InvalidRequestError, ValueError):
    """A workspace path is absolute, escapes its root or is otherwise not a relative file name."""


class ContextLengthError(PermanentError):
    """The prompt exceeded the model's context window. Compacting the history and trying again is the remedy, which
    is why this is not just a generic invalid request."""

    def __init__(
        self, message: str = "context length exceeded", *, limit: int | None = None
    ) -> None:
        super().__init__(message)
        self.limit = limit


class ContentFilterError(PermanentError):
    """The provider refused the request or response on content grounds."""


class AuthError(PermanentError):
    """The model provider rejected our credentials (a server misconfiguration, not the caller's sign-in)."""


class NonDeterminismError(PermanentError):
    """A replay reached a journaled step that does not match what the journal recorded there. Executing on would
    attach a stale result to a different call."""

    def __init__(self, message: str, *, path: str, expected: str, actual: str) -> None:
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.actual = actual


class OrphanedEffectError(PermanentError):
    """An effect was started and its outcome never recorded, and it is not safe to run again. The journaled intent
    is the record to compensate from."""

    def __init__(self, message: str, *, effect_id: str, kind: str) -> None:
        super().__init__(message)
        self.effect_id = effect_id
        self.kind = kind


# ── intentional halts ───────────────────────────────────────────────────────


class PolicyTermination(LoamzError):
    """An intentional, policy-enforced halt — not a bug and not a crash."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message


class BudgetExhaustedError(PolicyTermination):
    """A token, cost, turn or headcount budget ran out."""


class MiddlewareTermination(PolicyTermination):
    """A middleware halted the run (a guardrail, a rate limit)."""


__all__ = [
    "AgentCrashError",
    "AuthError",
    "BlockValidationError",
    "BranchAlreadyExistsError",
    "BranchHeadConflictError",
    "BranchNotFoundError",
    "BudgetExhaustedError",
    "CancellationError",
    "ConcurrentAppendError",
    "ContentFilterError",
    "ContextLengthError",
    "ControlSignal",
    "DAGIntegrityError",
    "LeaseLostError",
    "MiddlewareTermination",
    "NonDeterminismError",
    "OrphanedEffectError",
    "PolicyTermination",
    "SnapshotConflictError",
    "SuspendInterrupt",
    "ThreadBusyError",
    "ToolDeclarationError",
    "UnroutableMessageError",
    "UnsupportedContentError",
    "WorkspacePathError",
]
