"""Message types, envelope, and runtime configuration.

Pure data structures — no I/O, no external dependencies.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Literal

from raavan.core.runtime._protocol import AgentId, TopicId


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

    def __init__(self) -> None:
        self._cancelled = False
        self._futures: List[asyncio.Future[Any]] = []

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

    def link_future(self, future: asyncio.Future[Any]) -> None:
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
    """

    sender: AgentId | None
    target: AgentId | TopicId
    payload: Any
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MessageContext — passed to handlers so they can send replies
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Execution context provided to every message handler.

    Gives the handler access to the runtime (for sending replies or
    publishing follow-up messages) plus identity information.
    """

    runtime: Any  # AgentRuntime — kept as Any to avoid import cycle
    sender: AgentId | None
    correlation_id: str
    agent_id: AgentId


# ---------------------------------------------------------------------------
# Handler type alias
# ---------------------------------------------------------------------------

MessageHandler = Callable[[MessageContext, Any], Awaitable[Any]]
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
