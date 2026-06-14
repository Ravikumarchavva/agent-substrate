"""Message — the unit of agent-to-agent communication.

Every send or publish wraps a payload in a ``Message``.  The runtime routes
the message to its target and returns the recipient's reply.

``payload`` is a typed discriminated union (``Payload``).  The canonical
payload types are:

    ChatPayload         — a conversation turn (ChatMessage)
    ToolCallRequest     — a request to execute a tool       (defined in tools.py)
    ToolExecutionResult — the result of a tool execution    (defined in tools.py)
    DataPayload         — arbitrary JSON-serializable structured data
    ControlPayload      — runtime control signals (pause, cancel, handoff)
    ProgressPayload     — an AgentProgress event

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
    Literal,
    Union,
)

from pydantic import BaseModel, Field

from ravi.kernel.core.content import ChatMessage, JsonObject
from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.stream import AgentProgress
from ravi.kernel.tools import ToolCallRequest, ToolExecutionResult


# ---------------------------------------------------------------------------
# Payload types — the typed union of things a Message can carry
# ---------------------------------------------------------------------------


class ChatPayload(BaseModel):
    """A conversation turn."""

    kind: Literal["chat"] = "chat"
    message: ChatMessage

    model_config = {"frozen": True}


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
        ToolCallRequest,
        ToolExecutionResult,
        DataPayload,
        ControlPayload,
        ProgressPayload,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union of all canonical payload types.

``ToolCallRequest`` and ``ToolExecutionResult`` are defined in ``tools.py``
and imported here — tool types are owned by the tools module, not the
messaging module.

Custom payload kinds registered via ``register_payload_type()`` are
supported at runtime through the registry; the union above is for
static type-checking of the built-in kinds.
"""

# ---------------------------------------------------------------------------
# Payload registry — for custom kinds added by higher layers
# ---------------------------------------------------------------------------

_PAYLOAD_REGISTRY: dict[str, type[BaseModel]] = {
    "chat": ChatPayload,
    "tool_call": ToolCallRequest,
    "tool_result": ToolExecutionResult,
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
    reply_to: str | None = None  # run_id of the asker; set by RunContext.ask()

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_broadcast(self) -> bool:
        """True when this message is addressed to a topic (pub/sub fan-out)."""
        return isinstance(self.target, TopicId)


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
    "DataPayload",
    "ControlPayload",
    "ProgressPayload",
    "Payload",
    "register_payload_type",
    "ToolCallRequest",
    "ToolExecutionResult",
    "Message",
    "Subscription",
]
