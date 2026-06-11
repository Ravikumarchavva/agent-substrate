"""Message — the unit of agent-to-agent communication.

Every send or publish wraps a payload in a ``Message``.  The runtime routes
the message to its target and returns the recipient's reply.

``payload`` is a typed discriminated union (``Payload``).  The canonical
payload types are:

    ChatPayload      — a conversation turn (ChatMessage)
    ToolCallPayload  — a request to execute a tool
    ToolResultPayload— the result of a tool execution
    DataPayload      — arbitrary JSON-serializable structured data
    ControlPayload   — runtime control signals (pause, cancel, handoff)

Use ``register_payload_type()`` to add custom payload kinds without
modifying this file — the registry key is the ``kind`` discriminator string.

All message types are pydantic models so ``message.model_dump_json()``
round-trips cleanly for any transport (Kafka, NATS, Redis Streams,
Temporal, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import (
    Annotated,
    Any,
    Awaitable,
    Callable,
    Literal,
    Protocol,
    Union,
    runtime_checkable,
)

from pydantic import BaseModel, Field

from ravi.kernel.content import ChatMessage, JsonObject
from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.stream import AgentProgress


# ---------------------------------------------------------------------------
# Payload types — the typed union of things a Message can carry
# ---------------------------------------------------------------------------


class ChatPayload(BaseModel):
    """A conversation turn."""

    kind: Literal["chat"] = "chat"
    message: ChatMessage

    model_config = {"frozen": True}


class ToolCallPayload(BaseModel):
    """A request to execute a named tool."""

    kind: Literal["tool_call"] = "tool_call"
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    model_config = {"frozen": True}


class ToolResultPayload(BaseModel):
    """Result of a single tool execution."""

    kind: Literal["tool_result"] = "tool_result"
    call_id: str = ""
    name: str = ""
    content: list[Any] = Field(default_factory=list)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)
    structured_content: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}

    @property
    def text(self) -> str:
        from ravi.kernel.content import content_blocks_to_str

        return content_blocks_to_str(self.content)


class DataPayload(BaseModel):
    """Arbitrary JSON-serializable structured data."""

    kind: Literal["data"] = "data"
    data: JsonObject

    model_config = {"frozen": True}


class ControlPayload(BaseModel):
    """Runtime control signal — pause, cancel, handoff, etc."""

    kind: Literal["control"] = "control"
    signal: str
    data: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}


class ProgressPayload(BaseModel):
    """Payload carrying an agent progress event."""

    kind: Literal["progress"] = "progress"
    progress: AgentProgress

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


Payload = Annotated[
    Union[
        ChatPayload,
        ToolCallPayload,
        ToolResultPayload,
        DataPayload,
        ControlPayload,
        ProgressPayload,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union of all canonical payload types.

Custom payload kinds registered via ``register_payload_type()`` are
supported at runtime through the registry; the union above is for
static type-checking of the built-in kinds.
"""

# ---------------------------------------------------------------------------
# Payload registry — for custom kinds added by higher layers
# ---------------------------------------------------------------------------

_PAYLOAD_REGISTRY: dict[str, type[BaseModel]] = {
    "chat": ChatPayload,
    "tool_call": ToolCallPayload,
    "tool_result": ToolResultPayload,
    "data": DataPayload,
    "control": ControlPayload,
    "progress": ProgressPayload,
}


def register_payload_type(cls: type[BaseModel]) -> None:
    """Register a custom payload kind for runtime deserialization.

    ``cls`` must have a ``kind`` Literal field as the discriminator.
    Call once at module load time, before any messages are deserialized.
    """
    kind = getattr(cls, "kind", None)
    if kind is None:
        kind = getattr(cls.model_fields.get("kind"), "default", None)
    if not isinstance(kind, str):
        raise TypeError(f"{cls.__name__} must have a string 'kind' class attribute")
    _PAYLOAD_REGISTRY[kind] = cls


# ---------------------------------------------------------------------------
# Compatibility shims — kept in tools.py as the canonical source;
# re-exported here for call sites that import from message.
# ---------------------------------------------------------------------------

ToolCallRequest = ToolCallPayload
ToolExecutionResult = ToolResultPayload


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


class Message(BaseModel):
    """A routable unit of agent communication.

    ``id`` is a time-sortable hex identifier (UUID4 for now; ULID in a
    future pass).  It enables deduplication and idempotency at the transport
    layer.

    ``schema_version`` allows consumers to detect format changes when
    messages are persisted across deployments.

    ``target`` is always required — a ``Message`` with no destination cannot
    be delivered.  ``sender`` may be ``None`` for anonymous or bootstrap sends.

    ``correlation_id`` ties every message in one logical conversation;
    ``causation_id`` names the specific message that triggered this one.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    schema_version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    target: AgentId | TopicId
    payload: Payload
    sender: AgentId | None = None
    correlation_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    causation_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_broadcast(self) -> bool:
        """True when this message is addressed to a topic (pub/sub fan-out)."""
        return isinstance(self.target, TopicId)


# ---------------------------------------------------------------------------
# MessageContext — execution context handed to handlers
# ---------------------------------------------------------------------------


class MessageContext(BaseModel):
    """Execution context provided to every message handler invocation."""

    agent_id: AgentId
    sender: AgentId | None = None
    correlation_id: str = ""
    runtime: Any = None

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# MessageHandler — the handler callable signature
# ---------------------------------------------------------------------------

MessageHandler = Callable[[MessageContext, Payload], Awaitable[Payload | None]]
"""Type alias for a message handler: ``async def handle(ctx, payload) -> reply | None``."""


# ---------------------------------------------------------------------------
# Subscription — record of an active topic subscription
# ---------------------------------------------------------------------------


class Subscription(BaseModel):
    """Tracks a single active topic subscription."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    topic: TopicId
    agent_id: AgentId

    model_config = {"arbitrary_types_allowed": True}


__all__ = [
    "ChatPayload",
    "ToolCallPayload",
    "ToolResultPayload",
    "DataPayload",
    "ControlPayload",
    "ProgressPayload",
    "Payload",
    "register_payload_type",
    "ToolCallRequest",
    "ToolExecutionResult",
    "RuntimeRef",
    "Message",
    "MessageContext",
    "MessageHandler",
    "Subscription",
]
