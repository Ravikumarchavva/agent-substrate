"""Kernel exceptions — the typed vocabulary for failures and control flow.

::

    BaseException
    └── ControlSignal              unwinds the worker; never swallowed by `except Exception`
        ├── SuspendInterrupt       the run goes dormant until a wakeup
        ├── CancellationError      the run was cancelled, or its deadline passed
        └── LeaseLostError         another worker now owns this run; stop touching it

    KernelError                    carries `code` (stable id) and `retryable`
    ├── TransientError             retryable
    │   ├── ConcurrentAppendError
    │   ├── ThreadBusyError
    │   ├── BranchHeadConflictError
    │   ├── SnapshotConflictError
    │   └── RateLimitedError       carries `retry_after`
    ├── PermanentError             never worth retrying
    │   ├── AgentCrashError
    │   ├── BlockValidationError
    │   ├── UnsupportedContentError
    │   ├── BranchNotFoundError / BranchAlreadyExistsError / DAGIntegrityError
    │   ├── ObjectNotFoundError
    │   ├── ContextLengthError     the prompt exceeded the model's window
    │   ├── ContentFilterError     the provider refused on content grounds
    │   ├── AuthError              credentials rejected
    │   ├── NonDeterminismError    a replay diverged from its journal
    │   └── OrphanedEffectError    an effect started and its outcome was never recorded
    └── PolicyTermination          intentional halt, not a bug
        ├── BudgetExhaustedError
        └── MiddlewareTermination

Every exception can be pickled and copied. Most take keyword-only fields, which
the default exception pickling cannot rebuild; a failure that crosses a process
boundary (a worker pool, a job queue) used to arrive as a ``TypeError`` about
its own constructor instead of as the failure.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from substrate.types.identity import Actor

if TYPE_CHECKING:
    from substrate.types.error_info import ErrorInfo
    from substrate.types.wakeup import Wakeup


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _rebuild(cls: type, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return cls(*args, **kwargs)


class _Reconstructible:
    """Remembers its constructor arguments so pickle and copy can rebuild it."""

    _ctor: tuple[tuple[Any, ...], dict[str, Any]]

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        instance = super().__new__(cls, *args)  # type: ignore[call-arg]
        instance._ctor = (args, kwargs)
        return instance

    def __reduce__(self) -> tuple[Any, ...]:
        args, kwargs = self._ctor
        return (_rebuild, (type(self), args, kwargs))


# ---------------------------------------------------------------------------
# Control signals
# ---------------------------------------------------------------------------


class ControlSignal(_Reconstructible, BaseException):
    """Runtime control flow that must unwind to the worker.

    A ``BaseException`` so that a broad ``except Exception`` in agent or tool
    code cannot swallow suspension or cancellation.
    """


class SuspendInterrupt(ControlSignal):
    """Unwinds a run to the worker so it can go dormant until ``wakeup``."""

    def __init__(self, run_id: str, wakeup: "Wakeup", *, reason: str = "") -> None:
        super().__init__(f"run {run_id} suspended" + (f": {reason}" if reason else ""))
        self.run_id = run_id
        self.wakeup = wakeup
        self.reason = reason


class CancellationError(ControlSignal):
    """The run was cancelled, or its deadline passed."""


class LeaseLostError(ControlSignal):
    """This worker no longer owns the run: its lease expired and another worker
    holds it. Continuing would let two workers execute the same run."""

    def __init__(self, run_id: str, *, epoch: int | None = None) -> None:
        super().__init__(f"lease on run {run_id} was lost")
        self.run_id = run_id
        self.epoch = epoch


# ---------------------------------------------------------------------------
# Kernel errors
# ---------------------------------------------------------------------------


class KernelError(_Reconstructible, Exception):
    """Base of every typed runtime failure.

    ``code`` is a stable machine-readable identifier (``rate_limited``), derived
    from the class name unless overridden, so alerts and dashboards group
    failures by meaning. ``retryable`` says whether trying again can help.
    """

    retryable: ClassVar[bool] = False
    code: ClassVar[str] = "kernel"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "code" not in cls.__dict__:
            cls.code = _snake(cls.__name__.removesuffix("Error"))

    def to_info(self) -> "ErrorInfo":
        from substrate.types.error_info import ErrorInfo

        return ErrorInfo(code=self.code, message=str(self), retryable=self.retryable)


class TransientError(KernelError):
    """A failure that may succeed if tried again: a conflict, a blip, a limit."""

    retryable = True


class ConcurrentAppendError(TransientError):
    """``EventLog.append`` found the log had moved on: another writer won."""

    def __init__(self, message: str, *, run_id: str, expected_seq: int, actual_seq: int) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.expected_seq = expected_seq
        self.actual_seq = actual_seq


class ThreadBusyError(TransientError):
    """The thread already has an active run."""

    def __init__(self, message: str, *, thread_id: str) -> None:
        super().__init__(message)
        self.thread_id = thread_id


class BranchHeadConflictError(TransientError):
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


class SnapshotConflictError(TransientError):
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


class RateLimitedError(TransientError):
    """The provider is rate limiting us. ``retry_after`` is its own hint, in seconds."""

    def __init__(self, message: str = "rate limited", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(KernelError):
    """A failure that retrying cannot fix."""


class AgentCrashError(PermanentError):
    """An agent's run failed with an unexpected exception."""

    def __init__(self, message: str, *, run_id: str, agent_id: Actor) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.agent_id = agent_id

    def __str__(self) -> str:
        return f"[{self.agent_id} in run {self.run_id}] {super().__str__()}"


class ScopeViolationError(PermanentError):
    """A write tried to take over a record that belongs to a different scope.

    Record ids are chosen by callers — often copied from a request body or a model's
    output — so an id colliding with someone else's record must be refused rather than
    resolved by overwriting it."""

    def __init__(self, message: str, *, record_id: str) -> None:
        super().__init__(message)
        self.record_id = record_id


class ToolDeclarationError(PermanentError, ValueError):
    """A tool did not say how dangerous it is or whether it is safe to run twice.

    Both facts decide what the engine does — whether to ask a human first, whether a call
    that may or may not have happened can be repeated — so a tool that leaves them out is
    refused when it is registered, not guessed about when it is called."""

    def __init__(self, tool: str, problems: tuple[str, ...]) -> None:
        super().__init__(f"tool {tool!r} is not fit to register: " + "; ".join(problems))
        self.tool = tool
        self.problems = problems


class UnroutableMessageError(PermanentError):
    """A message arrived that no handler of the receiving agent accepts. Redelivering it
    cannot change that, so the run fails with the reason instead of retrying a poison message."""

    def __init__(self, message: str, *, agent: str, payload_type: str, accepts: tuple[str, ...]) -> None:
        super().__init__(message)
        self.agent = agent
        self.payload_type = payload_type
        self.accepts = accepts


class BlockValidationError(PermanentError, ValueError):
    """A content block failed validation."""


class UnsupportedContentError(PermanentError, ValueError):
    """A client cannot process this mix of content."""


class BranchNotFoundError(PermanentError, KeyError):
    """The requested branch does not exist."""


class BranchAlreadyExistsError(PermanentError, ValueError):
    """The branch id is already taken."""


class DAGIntegrityError(PermanentError, ValueError):
    """A cross-session edge, self-loop, invalid parent or other DAG corruption."""


class ObjectNotFoundError(PermanentError):
    """No object exists at the requested key."""

    def __init__(self, key: str) -> None:
        super().__init__(f"no object at {key!r}")
        self.key = key


class ContextLengthError(PermanentError):
    """The prompt exceeded the model's context window. Compacting the history and
    trying again is the remedy, which is why this is not just a generic 400."""

    def __init__(self, message: str = "context length exceeded", *, limit: int | None = None) -> None:
        super().__init__(message)
        self.limit = limit


class ContentFilterError(PermanentError):
    """The provider refused the request or response on content grounds."""


class AuthError(PermanentError):
    """The provider rejected our credentials."""


class NonDeterminismError(PermanentError):
    """A replay reached a journaled step that does not match what the journal
    recorded there. Executing on would attach a stale result to a different call."""

    def __init__(self, message: str, *, path: str, expected: str, actual: str) -> None:
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.actual = actual


class OrphanedEffectError(PermanentError):
    """An effect was started and its outcome never recorded, and it is not safe to
    run again. The journaled intent is the record to compensate from."""

    def __init__(self, message: str, *, effect_id: str, kind: str) -> None:
        super().__init__(message)
        self.effect_id = effect_id
        self.kind = kind


class PolicyTermination(KernelError):
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
    "KernelError",
    "LeaseLostError",
    "MiddlewareTermination",
    "NonDeterminismError",
    "ObjectNotFoundError",
    "OrphanedEffectError",
    "PermanentError",
    "PolicyTermination",
    "RateLimitedError",
    "SnapshotConflictError",
    "SuspendInterrupt",
    "ThreadBusyError",
    "TransientError",
    "ScopeViolationError",
    "ToolDeclarationError",
    "UnroutableMessageError",
    "UnsupportedContentError",
]
