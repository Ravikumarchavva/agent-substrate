"""CancellationToken (Protocol) and RunMeta — execution-scoped runtime metadata.

Both are threaded through every kernel API call so that:

- Any operation can be cancelled cooperatively (no global state).
- Distributed traces, deadlines, and tenant scoping are available
  everywhere without adding individual parameters to each call.

``CancellationToken`` here is a Protocol only — the concrete implementation
(real asyncio state: an ``Event``, a callback list) lives in
``agents/runtime/cancellation.py``, since kernel holds contracts, not
working implementations. ``RunMeta`` is a frozen value object; create one
per run() call — always with an already-constructed token from that layer,
never conjured here.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

from substrate.kernel.core.errors import CancellationError
from substrate.kernel.agent.supervision import Supervision


class CancellationToken(Protocol):
    """Cooperative cancellation signal for agent operations.

    Usage::

        token.cancel()                 # from outside: orchestrator, timeout, user
        token.check()                  # inside a coroutine: raises CancellationError if cancelled
        await token.wait()             # blocks until cancelled
        token.add_callback(lambda: ...)  # called synchronously on cancel
    """

    @property
    def is_cancelled(self) -> bool: ...

    def cancel(self, reason: str = "cancelled") -> None:
        """Signal cancellation. Idempotent — safe to call multiple times."""
        ...

    def check(self) -> None:
        """Raise ``CancellationError`` if this token has been cancelled.

        Call at cooperative yield points: before LLM calls, before tool
        execution, between loop iterations.
        """
        ...

    def wait(self) -> Awaitable[None]:
        """Block until the token is cancelled."""
        ...

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked synchronously when ``cancel()`` is called."""
        ...

    def child(self) -> "CancellationToken":
        """Return a child token that is cancelled when this one is.

        Cancelling the child does NOT cancel the parent.
        """
        ...


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Execution-scoped metadata threaded through every kernel call.

    ``run_id``       — globally unique identifier for this run; first-class so
                       every layer can key logs, effects, and EventLogProtocol entries
                       without digging into ``supervision``.  Populated from
                       ``supervision.run_id`` when supervision is provided.
    ``cancellation`` — cooperative cancellation; call ``check()`` at yield points.
    ``supervision``  — agent position in the execution tree; ``None`` for standalone runs.
    ``deadline``     — wall-clock expiry; agents and tools should honour it.
    ``trace_id``     — distributed trace identifier for observability.
    ``tenant_id``    — tenant namespace; ``None`` for single-tenant deployments.

    ``RunMeta`` is immutable.  Thread it down call stacks instead of
    mutating it.  For child spans create a new ``RunMeta`` with a child
    token (so cancellation propagates down) and new trace span.
    """

    run_id: str
    cancellation: CancellationToken
    supervision: Supervision | None = None
    deadline: datetime | None = None
    trace_id: str = field(default_factory=lambda: _uuid.uuid4().hex)
    tenant_id: str | None = None

    def check(self) -> None:
        """Raise CancellationError if cancelled or deadline expired."""
        self.cancellation.check()
        if self.deadline is not None and datetime.now(timezone.utc) > self.deadline:
            raise CancellationError("deadline exceeded")

    def is_expired(self) -> bool:
        if self.cancellation.is_cancelled:
            return True
        if self.deadline is not None and datetime.now(timezone.utc) > self.deadline:
            return True
        return False

    def child_token(self) -> CancellationToken:
        """Return a child token cancelled when this context is cancelled."""
        return self.cancellation.child()


__all__ = ["CancellationToken", "RunMeta"]
