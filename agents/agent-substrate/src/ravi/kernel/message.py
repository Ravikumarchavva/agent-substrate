"""Message — the unit of agent-to-agent communication.

Every send or publish wraps a payload in a ``Message``.  The runtime routes
the message to its target and returns the recipient's reply.

``payload`` is intentionally ``object`` — the kernel does not enforce a
specific type at the routing layer.  ``list[ContentBlock]`` is the
conventional payload for multimodal content, but agents can exchange any
serializable object (events, commands, ACKs).

Tracing / distributed spans are a fabric/infrastructure concern and are NOT
part of this type.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, runtime_checkable

from ravi.kernel.identity import AgentId, TopicId


# ---------------------------------------------------------------------------
# RuntimeRef — the minimal runtime slice visible to handlers
# ---------------------------------------------------------------------------


@runtime_checkable
class RuntimeRef(Protocol):
    """The slice of the runtime a message handler uses to reply or emit."""

    async def send_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        recipient: AgentId,
    ) -> object: ...

    async def publish_message(
        self,
        payload: object,
        *,
        sender: AgentId | None = None,
        topic: TopicId,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Message:
    """A routable unit of agent communication.

    ``target`` is always required — a ``Message`` with no destination cannot
    be delivered.  ``sender`` may be ``None`` for anonymous or bootstrap sends.

    ``correlation_id`` ties every message in one logical conversation;
    ``causation_id`` names the specific message that triggered this one.

    ``metadata`` is a flat string→string map for lightweight tags
    (e.g. ``{"priority": "high"}``).  Use ``payload`` for structured content.
    """

    target: AgentId | TopicId       # required — routing destination
    payload: object                  # the actual message content
    sender: AgentId | None = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    causation_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_broadcast(self) -> bool:
        """True when this message is addressed to a topic (pub/sub fan-out)."""
        return isinstance(self.target, TopicId)


# ---------------------------------------------------------------------------
# MessageContext — execution context handed to handlers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Execution context provided to every message handler invocation."""

    runtime: RuntimeRef
    sender: AgentId | None
    correlation_id: str
    agent_id: AgentId       # identity of the receiving agent


# ---------------------------------------------------------------------------
# MessageHandler — the handler callable signature
# ---------------------------------------------------------------------------

MessageHandler = Callable[[MessageContext, object], Awaitable[object]]
"""Type alias for a message handler: ``async def handle(ctx, payload) -> reply``."""


# ---------------------------------------------------------------------------
# Subscription — record of an active topic subscription
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Subscription:
    """Tracks a single active topic subscription."""

    id: str
    topic: TopicId
    agent_type: str


__all__ = [
    "RuntimeRef",
    "Message",
    "MessageContext",
    "MessageHandler",
    "Subscription",
]
