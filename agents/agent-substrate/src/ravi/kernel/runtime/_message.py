"""Runtime message contracts — the unit of agent-to-agent communication.

Agent↔agent communication is synchronous full-message request/response:
a sender delivers a complete :class:`Envelope` and receives the recipient's
complete reply. No chunks on this path — streaming is a separate, user-facing
visibility concern (see :mod:`ravi.kernel.runtime._stream`).

Types:
    Envelope        — wraps a message with routing + causal metadata
    RuntimeRef      — minimal runtime view handed to handlers
    MessageContext  — execution context passed to a message handler
    MessageHandler  — typed handler callable signature
    Subscription    — record of a topic subscription
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, runtime_checkable

from ravi.kernel.messages.content import ContentBlock, JsonObject
from ravi.kernel.runtime._identity import AgentId, TopicId


@runtime_checkable
class RuntimeRef(Protocol):
    """The slice of the runtime a message handler needs to reply or emit."""

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


@dataclass(slots=True)
class Envelope:
    """Wraps a message flowing between agents.

    ``target`` is the routing key — an :class:`AgentId` for point-to-point
    delivery or a :class:`TopicId` for pub/sub fan-out. ``content`` carries
    the message as typed :class:`ContentBlock` objects (always a full message,
    never a chunk).

    ``correlation_id`` ties together every message in one logical conversation;
    ``causation_id`` names the specific message that caused this one.
    ``trace_id``/``trace_context`` propagate distributed tracing across hops.
    """

    sender: AgentId | None
    target: AgentId | TopicId | None
    content: list[ContentBlock]
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    causation_id: str | None = None
    trace_id: str | None = None
    trace_context: dict[str, str] = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Execution context provided to every message handler."""

    runtime: RuntimeRef
    sender: AgentId | None
    correlation_id: str
    agent_id: AgentId


MessageHandler = Callable[[MessageContext, object], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class Subscription:
    """Tracks a single topic subscription."""

    id: str
    topic: TopicId
    agent_type: str


__all__ = [
    "Envelope",
    "RuntimeRef",
    "MessageContext",
    "MessageHandler",
    "Subscription",
]
