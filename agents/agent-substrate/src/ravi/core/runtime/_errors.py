"""All runtime-specific exceptions — single source of truth.

Consolidating errors into one module avoids circular imports and makes
it trivial for consumers to ``except`` the right thing::

    from ravi.core.runtime._errors import AgentNotFoundError, HandlerError
"""

from __future__ import annotations


class AgentNotFoundError(Exception):
    """Raised when dispatching to an ``AgentId`` that has no registered mailbox."""


class HandlerError(Exception):
    """Raised when a message handler crashes.

    Wraps the original exception so callers of ``send_message`` receive
    a proper error instead of a silent ``None``.
    """


class MailboxFullError(Exception):
    """Raised when a non-blocking ``put_nowait`` finds the mailbox at capacity."""


class SupervisorEscalation(Exception):
    """Raised when an agent exceeds its restart budget."""


# ---------------------------------------------------------------------------
# Resource locking errors
# ---------------------------------------------------------------------------


class ResourceConflictError(Exception):
    """Raised when an agent cannot acquire a lock on a shared resource.

    Contains the resource URI and the agent that currently holds the lock.
    """

    def __init__(self, resource_uri: str, holder_agent_id: str, message: str = "") -> None:
        self.resource_uri = resource_uri
        self.holder_agent_id = holder_agent_id
        super().__init__(message or f"resource {resource_uri!r} locked by {holder_agent_id}")


class DeadlockDetectedError(Exception):
    """Raised when the lock manager detects a wait-for cycle.

    Contains the list of agent IDs forming the deadlock cycle.
    """

    def __init__(self, cycle: list[str], message: str = "") -> None:
        self.cycle = cycle
        super().__init__(message or f"deadlock detected: {' → '.join(cycle)}")


# ---------------------------------------------------------------------------
# Saga / critical action errors
# ---------------------------------------------------------------------------


class SagaFailedError(Exception):
    """Raised when a saga cannot complete and compensating actions are needed.

    ``completed_steps`` lists step IDs that succeeded before the failure.
    ``failed_step`` is the step that caused the saga to abort.
    """

    def __init__(
        self,
        saga_id: str,
        failed_step: str,
        completed_steps: list[str] | None = None,
        message: str = "",
    ) -> None:
        self.saga_id = saga_id
        self.failed_step = failed_step
        self.completed_steps = completed_steps or []
        super().__init__(message or f"saga {saga_id!r} failed at step {failed_step!r}")


# ---------------------------------------------------------------------------
# Checkpoint errors
# ---------------------------------------------------------------------------


class CheckpointCorruptedError(Exception):
    """Raised when a checkpoint cannot be deserialized or is inconsistent."""


class EnvelopeExpiredError(Exception):
    """Raised when an envelope's TTL has been exceeded."""

