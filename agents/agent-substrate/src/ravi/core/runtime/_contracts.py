"""Runtime contracts — typed data structures for agent communication.

Every piece of data flowing through the runtime is defined here with
strict types.  This module has zero ``Any`` in its public API.

Types:
    Envelope        — wraps every message with routing metadata
    MessageContext  — execution context passed to message handlers
    MessageHandler  — typed handler callable signature
    CancellationToken — cooperative async cancellation
    Subscription    — tracks a topic subscription
    StreamDone      — sentinel for end-of-stream
    RestartPolicy   — supervisor restart configuration
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Literal, Protocol, runtime_checkable

from ravi.core.messages.content import JsonObject
from ravi.core.messages.content import ContentBlock  # noqa: F401 — used in Envelope type annotation
from ravi.core.runtime._identity import AgentId, TopicId


# ---------------------------------------------------------------------------
# RuntimeRef — typed protocol replacing ``runtime: Any`` in MessageContext
# ---------------------------------------------------------------------------


@runtime_checkable
class RuntimeRef(Protocol):
    """Minimal runtime interface visible to message handlers.

    This protocol breaks the circular dependency between ``MessageContext``
    and the full ``AgentRuntime`` protocol.  Handlers only need
    ``send_message`` and ``publish_message`` to send replies.
    """

    async def send_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object: ...

    async def publish_message(
        self,
        message: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None: ...


# ---------------------------------------------------------------------------
# CancellationToken — cooperative cancellation for in-flight operations
# ---------------------------------------------------------------------------


class CancellationToken:
    """Cooperative cancellation token for ``send_message`` calls.

    Usage::

        token = CancellationToken()
        task = asyncio.create_task(runtime.send_message(..., cancellation_token=token))
        # ... later ...
        token.cancel()   # cancels the linked future
    """

    __slots__ = ("_cancelled", "_futures")

    def __init__(self) -> None:
        self._cancelled = False
        self._futures: list[asyncio.Future[object]] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Mark this token as cancelled and cancel all linked futures."""
        self._cancelled = True
        for f in self._futures:
            if not f.done():
                f.cancel()
        self._futures.clear()

    def link_future(self, future: asyncio.Future[object]) -> None:
        """Link *future* so it is cancelled when this token fires."""
        if self._cancelled:
            if not future.done():
                future.cancel()
            return
        self._futures.append(future)


# ---------------------------------------------------------------------------
# Envelope — the unit of communication between agents
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Envelope:
    """Wraps every message flowing through the runtime.

    ``target`` is either an ``AgentId`` (point-to-point) or a ``TopicId``
    (pub/sub broadcast).

    ``content`` carries the actual message data as a list of typed
    ``ContentBlock`` objects — the universal multimodal primitive.  Every
    piece of data in the runtime (text, images, tool calls, errors) is
    represented as content blocks.

    Tracing fields:
        ``correlation_id`` — groups request/response pairs.
        ``causation_id``   — which envelope caused this one (causal chain).
        ``trace_id``       — spans an entire distributed execution tree.

    QoS fields:
        ``priority`` — higher priority envelopes are dequeued first (0 = normal).
        ``ttl``      — seconds until the envelope expires (None = never).
    """

    sender: AgentId | None
    target: AgentId | TopicId
    content: list["ContentBlock"]
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    causation_id: str | None = None
    trace_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonObject = field(default_factory=dict)
    priority: int = 0
    ttl: float | None = None

    @property
    def is_expired(self) -> bool:
        """Return True if the envelope has exceeded its TTL."""
        if self.ttl is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.created_at).total_seconds()
        return elapsed > self.ttl


# ---------------------------------------------------------------------------
# MessageContext — passed to handlers so they can send replies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Execution context provided to every message handler.

    Gives the handler access to the runtime (for sending replies or
    publishing follow-up messages) plus identity information.

    ``runtime`` is typed as ``RuntimeRef`` — a minimal protocol that
    breaks the circular dependency with the full ``AgentRuntime``.
    """

    runtime: RuntimeRef
    sender: AgentId | None
    correlation_id: str
    agent_id: AgentId


# ---------------------------------------------------------------------------
# Handler type alias
# ---------------------------------------------------------------------------

MessageHandler = Callable[[MessageContext, object], Awaitable[object]]
"""Signature of an agent's message-processing function.

Receives ``(context, payload)`` and returns a response value (or ``None``
for fire-and-forget topics).
"""


# ---------------------------------------------------------------------------
# Subscription record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Subscription:
    """Tracks a single topic subscription."""

    id: str
    topic: TopicId
    agent_type: str


# ---------------------------------------------------------------------------
# StreamDone sentinel — signals end of a streaming topic
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamDone:
    """Sentinel published to a ``TopicId`` to signal the stream has ended.

    Subscribers check ``isinstance(payload, StreamDone)`` to know when
    to stop consuming.
    """

    reason: str = "complete"


# ---------------------------------------------------------------------------
# Supervisor configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    """Erlang-style restart policy for supervised agents.

    ``max_restarts`` within ``restart_window`` seconds before the supervisor
    escalates (raises ``SupervisorEscalation``).
    """

    max_restarts: int = 3
    restart_window: float = 60.0
    strategy: Literal["one_for_one", "one_for_all"] = "one_for_one"
