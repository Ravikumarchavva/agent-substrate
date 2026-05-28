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
from typing import Any, Awaitable, Callable, Literal, Protocol, TYPE_CHECKING, runtime_checkable

from ravi.kernel.contracts._coordination import LocalityHint, TemporalSemantics, TrustContext, PlacementContract
from ravi.kernel.contracts._trust import ProvenanceChain, PrincipalTrustContext  # noqa: F401 — PrincipalTrustContext available for callers
from ravi.kernel.messages.content import JsonObject
from ravi.kernel.messages.content import ContentBlock  # noqa: F401 — used in Envelope type annotation
from ravi.kernel.runtime._identity import AgentId, IdentityContext, TopicId
from ravi.kernel.runtime._lifecycle import AgentActivationContract


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

    Carries the same fabric metadata as
    :class:`ravi.kernel.contracts.EventEnvelope` (identity, trust, provenance,
    activation, placement, temporal, locality, trace context) so the runtime
    can serialize losslessly to the wire format via :meth:`to_event_envelope`.

    ``target`` is the in-process routing key (``AgentId`` for point-to-point
    or ``TopicId`` for pub/sub). ``None`` is allowed on wire-originated
    envelopes that have not yet been bound to a routing target.

    ``content`` carries the actual message data as a list of typed
    ``ContentBlock`` objects — the universal multimodal primitive.
    """

    sender: AgentId | None
    target: AgentId | TopicId | None
    content: list["ContentBlock"]
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    causation_id: str | None = None
    trace_id: str | None = None
    trace_context: dict[str, str] = field(default_factory=dict)
    temporal: TemporalSemantics = field(default_factory=TemporalSemantics)
    locality: LocalityHint = field(default_factory=LocalityHint)
    metadata: JsonObject = field(default_factory=dict)
    priority: int = 0
    trust: TrustContext | None = None
    provenance: ProvenanceChain | None = None
    identity: IdentityContext | None = None
    activation: AgentActivationContract | None = None
    placement: PlacementContract | None = None
    # Wire-compat fields (mirror EventEnvelope)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    event_type: str = ""
    event_version: int = 1
    tenant_id: str = "default"
    workspace_id: str = "default"
    actor_id: str = ""

    def __post_init__(self) -> None:
        self.temporal.bind_defaults()
        # Derive tenancy from identity when not explicitly set.
        if self.identity is not None:
            if self.tenant_id == "default":
                self.tenant_id = self.identity.effective_tenant_id
            if self.workspace_id == "default":
                self.workspace_id = self.identity.effective_workspace_id
            if not self.actor_id:
                self.actor_id = self.identity.principal.fqn

    @property
    def is_expired(self) -> bool:
        """Return True if the envelope has exceeded its TTL."""
        return self.temporal.is_expired(now=datetime.now(timezone.utc))

    @property
    def is_ready(self) -> bool:
        """Return True if the envelope is eligible for delivery now."""
        return self.temporal.is_ready(now=datetime.now(timezone.utc))

    def to_event_envelope(self, *, event_type: str | None = None) -> "EventEnvelopeAny":
        """Serialize this in-process envelope to the canonical wire format.

        The payload is the content blocks themselves. ``event_type`` defaults
        to this envelope's ``event_type`` field; pass an explicit value for
        envelopes that haven't set one yet.
        """
        from ravi.kernel.contracts._event import EventEnvelope

        resolved_event_type = event_type or self.event_type
        if not resolved_event_type:
            raise ValueError(
                "Envelope.to_event_envelope requires an event_type; set "
                "Envelope.event_type before calling or pass event_type=..."
            )

        return EventEnvelope[list[ContentBlock]](
            event_id=self.event_id,
            event_type=resolved_event_type,
            event_version=self.event_version,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            trace_id=self.trace_id,
            trace_context=dict(self.trace_context),
            temporal=self.temporal,
            locality=self.locality,
            identity=self.identity,
            trust=self.trust,
            provenance=self.provenance,
            activation=self.activation,
            placement=self.placement,
            priority=self.priority,
            metadata=dict(self.metadata),
            payload=list(self.content),
        )


# Forward-ref alias for the return type of ``to_event_envelope``. We avoid
# importing ``EventEnvelope`` eagerly to keep this module self-contained,
# but type-checkers see the real class.
if TYPE_CHECKING:
    from ravi.kernel.contracts._event import EventEnvelope as _EventEnvelopeType

    EventEnvelopeAny = _EventEnvelopeType[list["ContentBlock"]]
else:
    EventEnvelopeAny = Any


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
