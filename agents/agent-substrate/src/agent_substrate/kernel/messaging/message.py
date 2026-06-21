"""Message — the unit of agent-to-agent communication.

Every send or publish wraps a payload in a ``Message``.  The runtime routes
the message to its target and returns the recipient's reply.

``payload`` accepts any ``PayloadBase`` subclass.  Built-in kinds:

    ChatPayload         — a conversation turn (ChatMessage)
    ToolCallRequest     — a request to execute a tool       (defined in tools.py)
    ToolExecutionResult — the result of a tool execution    (defined in tools.py)
    DataPayload         — arbitrary JSON-serializable structured data
    ControlPayload      — runtime control signals (pause, cancel, handoff)
    ProgressPayload     — an AgentProgress event

Use ``register_payload_type()`` to add custom payload kinds at runtime.
Custom types **must** subclass ``PayloadBase`` — this is enforced at
registration time so deserialization is always safe.

All message types are pydantic models so ``message.model_dump_json()``
round-trips cleanly for any transport (Kafka, NATS, Redis Streams, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, SerializeAsAny, field_validator

from agent_substrate.kernel.core.content import ChatMessage, JsonObject
from agent_substrate.kernel.core.identity import AgentId, TopicId
from agent_substrate.kernel.messaging.stream import AgentProgress
from agent_substrate.kernel.tools import PayloadBase, ToolCallRequest, ToolExecutionResult


# ---------------------------------------------------------------------------
# Built-in payload types  (all inherit PayloadBase)
# ---------------------------------------------------------------------------


class ChatPayload(PayloadBase):
    """A conversation turn."""

    kind: Literal["chat"] = "chat"
    message: ChatMessage


class DataPayload(PayloadBase):
    """Arbitrary JSON-serializable structured data."""

    kind: Literal["data"] = "data"
    data: JsonObject


class ControlPayload(PayloadBase):
    """Runtime control signal — pause, cancel, handoff, etc."""

    kind: Literal["control"] = "control"
    signal: str
    data: JsonObject = Field(default_factory=dict)


class ProgressPayload(PayloadBase):
    """Payload carrying an agent progress event."""

    kind: Literal["progress"] = "progress"
    progress: AgentProgress
    model_config = {"frozen": True, "arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Payload type alias
#
# SerializeAsAny ensures subclass-specific fields survive model_dump / JSON
# round-trips even though the annotation names only the base class.
# ---------------------------------------------------------------------------

Payload = SerializeAsAny[PayloadBase]

# ---------------------------------------------------------------------------
# Payload registry — maps kind string → concrete PayloadBase subclass
# ---------------------------------------------------------------------------

_PAYLOAD_REGISTRY: dict[str, type[PayloadBase]] = {
    "chat": ChatPayload,
    "tool_call": ToolCallRequest,
    "tool_result": ToolExecutionResult,
    "data": DataPayload,
    "control": ControlPayload,
    "progress": ProgressPayload,
}


def register_payload_type(cls: type[BaseModel]) -> None:
    """Register a custom payload kind for runtime deserialization.

    ``cls`` must subclass ``PayloadBase`` and have a ``kind`` Literal field.
    Call once at module load time, before any messages of that kind arrive.
    """
    if not (isinstance(cls, type) and issubclass(cls, PayloadBase)):
        raise TypeError(f"{cls.__name__} must subclass PayloadBase")
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

    @field_validator("payload", mode="before")
    @classmethod
    def _parse_payload(cls, v: Any) -> Any:
        """Dispatch dict payloads to the registry; validate instance payloads."""
        if isinstance(v, PayloadBase):
            if v.kind in _PAYLOAD_REGISTRY:
                return v
            raise ValueError(f"Unregistered payload type: {type(v).__name__!r}")
        if isinstance(v, dict):
            kind = v.get("kind")
            model = _PAYLOAD_REGISTRY.get(kind)
            if model is None:
                raise ValueError(f"Unknown payload kind: {kind!r}")
            return model.model_validate(v)
        raise TypeError(
            f"payload must be a PayloadBase subclass or dict, got {type(v).__name__!r}"
        )

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
    "PayloadBase",
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
