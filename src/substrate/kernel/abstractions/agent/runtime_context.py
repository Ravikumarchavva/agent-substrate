"""CancellationTokenProtocol and RunMeta — execution-scoped runtime metadata.

Both are threaded through every kernel API call so that:

- Any operation can be cancelled cooperatively (no global state).
- Distributed traces, deadlines, and tenant scoping are available
  everywhere without adding individual parameters to each call.

``CancellationTokenProtocol`` here is a Protocol only — the concrete
implementation (real asyncio state: an ``Event``, a callback list) lives in
``agents/runtime/cancellation.py::CancellationToken``, since kernel holds
contracts, not working implementations. Named distinctly (not
``CancellationToken``) so the Protocol and its one implementation can never
collide under the same bare name on a dual import. ``RunMeta`` is a frozen
value object; create one per run() call — always with an already-constructed
token from that layer, never conjured here.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping, Protocol, runtime_checkable

from substrate.kernel.exceptions import CancellationError
from substrate.kernel.agent.supervision import Supervision


@runtime_checkable
class CancellationTokenProtocol(Protocol):
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

    def child(self) -> "CancellationTokenProtocol":
        """Return a child token that is cancelled when this one is.

        Cancelling the child does NOT cancel the parent.
        """
        ...


@dataclass(frozen=True, slots=True)
class RunScope:
    """Whose work this is, for the inbound message being handled.

    One run can handle several inbound messages, so this is per-message
    state: ``RunContext.scope`` holds the current one. Tools read it from
    ``ctx`` (see ``scope_of``) — never from ambient globals, and never from
    model-supplied arguments: the model can be steered by the documents it
    reads, so an identity it supplies is an authorization hole.

    ``thread_id`` is the conversation that owns files, documents and task
    boards; empty when there isn't one. A sub-agent inherits its parent's (it
    works inside the same conversation), even though its own message history
    lives under a separate, run-scoped session id.
    """

    tenant_id: str | None = None
    user_id: str | None = None
    thread_id: str = ""
    branch_id: str = "main"
    agent_id: str = ""
    agent_label: str = ""
    parent_agent_id: str | None = None

    @classmethod
    def from_metadata(
        cls,
        metadata: Mapping[str, object],
        *,
        thread_id: str,
        agent_id: str,
        agent_label: str,
    ) -> "RunScope":
        """Scope for a message: identity and conversation from its metadata
        (stamped by the caller, or by a parent via ``child_metadata``), the rest
        from the agent. *thread_id* is the fallback conversation — the
        message's own session — when metadata doesn't name one."""

        def _str(key: str) -> str | None:
            value = metadata.get(key)
            return str(value) if value else None

        return cls(
            tenant_id=_str("tenant_id"),
            user_id=_str("user_id"),
            thread_id=_str("thread_id") or thread_id,
            branch_id=_str("branch_id") or "main",
            agent_id=agent_id,
            agent_label=agent_label,
            parent_agent_id=_str("parent_agent_id"),
        )

    def child_metadata(self) -> dict[str, str]:
        """Message metadata that makes a spawned child inherit this scope —
        same tenant, user, conversation and branch — with this agent as its parent."""
        inherited = {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "branch_id": self.branch_id,
            "parent_agent_id": self.agent_id or None,
        }
        return {k: v for k, v in inherited.items() if v}


def scope_of(ctx: object | None) -> RunScope:
    """The scope carried by *ctx* (a ``RunMeta`` or a ``RunContext``), or an
    empty one when there is no run context (a bare tool call in a test)."""
    scope = getattr(ctx, "scope", None)
    return scope if isinstance(scope, RunScope) else RunScope()


@dataclass(frozen=True, slots=True)
class RunMeta:
    """Execution-scoped metadata threaded through every kernel call.

    ``run_id``       — globally unique identifier for this run; first-class so
                       every layer can key logs, effects, and EventLogProtocol entries
                       without digging into ``supervision``.  Populated from
                       ``supervision.run_id`` when supervision is provided.
    ``cancellation`` — cooperative cancellation; call ``check()`` at yield points.
    ``supervision``  — agent position in the execution tree; ``None`` for standalone runs.
    ``deadline``     — wall-clock expiry; agents and tools should honour it. Resolved
                       from ``supervision.execution_budget.deadline_s`` at run start
                       (see ``agents/core/react.py::_resolve_execution_budget``) —
                       this is the one absolute cutoff every ``check()`` call enforces;
                       ``SchedulerProtocol.enqueue``'s own ``deadline`` parameter is a
                       distinct, scheduler-level lease/queueing cutoff, not this one.
    ``trace_id``     — distributed trace identifier for observability.
    ``tenant_id``    — tenant namespace; ``None`` for single-tenant deployments.
    ``scope``        — who/where the message being handled belongs to; see ``RunScope``.

    ``RunMeta`` is immutable.  Thread it down call stacks instead of
    mutating it.  For child spans create a new ``RunMeta`` with a child
    token (so cancellation propagates down) and new trace span.
    """

    run_id: str
    cancellation: CancellationTokenProtocol
    supervision: Supervision | None = None
    deadline: datetime | None = None
    trace_id: str = field(default_factory=lambda: _uuid.uuid4().hex)
    tenant_id: str | None = None
    scope: RunScope = field(default_factory=RunScope)

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

    def child_token(self) -> CancellationTokenProtocol:
        """Return a child token cancelled when this context is cancelled."""
        return self.cancellation.child()


__all__ = ["CancellationTokenProtocol", "RunMeta"]
