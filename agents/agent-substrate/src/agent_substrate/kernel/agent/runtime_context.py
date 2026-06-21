"""CancellationToken and RunMeta — execution-scoped runtime metadata.

Both types are threaded through every kernel API call so that:

- Any operation can be cancelled cooperatively (no global state).
- Distributed traces, deadlines, and tenant scoping are available
  everywhere without adding individual parameters to each call.

``CancellationToken`` is pure asyncio — no I/O, no threads.
``RunMeta`` is a frozen value object; create one per run() call.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from agent_substrate.kernel.core.errors import CancellationError
from agent_substrate.kernel.runtime.ids import new_run_id
from agent_substrate.kernel.agent.supervision import Supervision


class CancellationToken:
    """Cooperative cancellation signal for agent operations.

    Usage::

        token = CancellationToken()

        # From outside (orchestrator, timeout handler, user):
        token.cancel()

        # Inside any coroutine:
        token.check()         # raises CancellationError if cancelled
        await token.wait()    # blocks until cancelled

        # Register a callback (called synchronously on cancel):
        token.add_callback(lambda: ...)
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._event = asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []

    def cancel(self, reason: str = "cancelled") -> None:
        """Signal cancellation. Idempotent — safe to call multiple times."""
        if not self._cancelled:
            self._cancelled = True
            self._reason = reason
            self._event.set()
            for cb in self._callbacks:
                try:
                    cb()
                except Exception:
                    pass

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        """Raise ``CancellationError`` if this token has been cancelled.

        Call at cooperative yield points: before LLM calls, before tool
        execution, between loop iterations.
        """
        if self._cancelled:
            raise CancellationError(getattr(self, "_reason", "cancelled"))

    async def wait(self) -> None:
        """Block until the token is cancelled."""
        await self._event.wait()

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback invoked synchronously when ``cancel()`` is called."""
        if self._cancelled:
            callback()
        else:
            self._callbacks.append(callback)

    def child(self) -> "CancellationToken":
        """Return a child token that is cancelled when this one is.

        Cancelling the child does NOT cancel the parent.
        """
        child_token = CancellationToken()
        self.add_callback(lambda: child_token.cancel("parent cancelled"))
        return child_token


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Execution-scoped metadata threaded through every kernel call.

    ``run_id``       — globally unique identifier for this run; first-class so
                       every layer can key logs, effects, and EventLog entries
                       without digging into ``supervision``.  Populated from
                       ``supervision.run_id`` when supervision is provided;
                       generated fresh by ``standalone()``.
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

    @classmethod
    def standalone(
        cls,
        *,
        run_id: str = "",
        trace_id: str = "",
        deadline: datetime | None = None,
        tenant_id: str | None = None,
    ) -> "RunMeta":
        """Create a standalone RunMeta with a fresh CancellationToken."""
        return cls(
            run_id=run_id or new_run_id(),
            cancellation=CancellationToken(),
            deadline=deadline,
            trace_id=trace_id or _uuid.uuid4().hex,
            tenant_id=tenant_id,
        )

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
