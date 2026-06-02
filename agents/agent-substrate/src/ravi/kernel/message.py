"""Message — the unit of agent-to-agent communication.

Every send or publish wraps a payload in a ``Message``.  The runtime routes
the message to its target and returns the recipient's reply.

``payload`` is intentionally ``object`` — the kernel does not enforce a
specific type at the routing layer.  ``list[ContentBlock]`` is the
conventional payload for multimodal content, but agents can exchange any
serializable object (events, commands, ACKs, tool results).

Tool call/result types live here because they are message payloads that flow
between the LLM, the agent, and the tool executor — they are communication
primitives, not tool implementation details.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, Field

from ravi.kernel.content import ContentBlock, JsonObject
from ravi.kernel.identity import AgentId, TopicId


# ---------------------------------------------------------------------------
# Tool call / result — message payloads exchanged during tool execution
# ---------------------------------------------------------------------------


class ToolCallRequest(BaseModel):
    """A request to execute a named tool — sent from agent to tool executor."""

    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid4()))

    model_config = {"frozen": True}


class ToolExecutionResult(BaseModel):
    """Result of a single tool execution — returned to the agent."""

    call_id: str = ""
    name: str = ""
    content: list[ContentBlock] = Field(default_factory=list)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True, "frozen": False}

    @property
    def text(self) -> str:
        """Human-readable lowering of all content blocks."""
        from ravi.kernel.content import content_blocks_to_str
        return content_blocks_to_str(self.content)


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
    (e.g. ``{"trace_id": "abc123"}``).  Use ``payload`` for structured content.
    Agent priority lives on ``Supervision.priority``, not here.
    """

    target: AgentId | TopicId
    payload: object
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
    agent_id: AgentId


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
    agent_id: AgentId


__all__ = [
    # Tool message payloads
    "ToolCallRequest",
    "ToolExecutionResult",
    # Runtime protocol
    "RuntimeRef",
    # Message envelope
    "Message",
    "MessageContext",
    "MessageHandler",
    "Subscription",
]
