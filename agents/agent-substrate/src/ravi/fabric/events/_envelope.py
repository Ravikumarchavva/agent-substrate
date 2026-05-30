"""Generic typed event envelope — canonical domain event contract.

``EventEnvelope[T]`` is the **single canonical wire format** for every
event leaving a runtime process: cross-service streams, durable logs,
realtime fanout, and replay all use the same shape. The in-runtime
``Envelope`` (``ravi.kernel.runtime._contracts``) carries the same rich
fabric metadata and bridges to this wire format losslessly via
:meth:`Envelope.to_event_envelope` and :meth:`EventEnvelope.to_runtime_envelope`.

Both envelopes carry the full hyperscale context:

- **Identity / trust / provenance** — who is acting, how trusted, with what lineage
- **Activation contract**           — lifecycle state, lease, depth limits
- **Placement contract**            — region / shard / data-gravity hints
- **Temporal semantics**            — event time, delivery window, replay flag
- **Locality hint**                 — fast routing hint distinct from full placement

Any field a worker sets on the runtime ``Envelope`` survives the trip to the
wire and back — there is no second-tier envelope that strips fabric context.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

from ravi.kernel.contracts._coordination import (
    LocalityHint,
    PlacementContract,
    TemporalSemantics,
    TrustContext,
)
from ravi.kernel.contracts._trust import ProvenanceChain
from ravi.kernel.runtime._identity import IdentityContext
from ravi.kernel.runtime._lifecycle import AgentActivationContract

if TYPE_CHECKING:
    from ravi.kernel.runtime._contracts import Envelope

T = TypeVar("T")


class EventEnvelope(BaseModel, Generic[T]):
    """Typed domain event envelope — the canonical wire format.

    ``T`` is the payload type. Use ``dict[str, Any]`` for unstructured
    events; use a specific Pydantic model for fully-typed domain events.

    All inter-service async events are wrapped in this envelope before
    being published to the durable log or realtime fanout.
    """

    # Core identity
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_version: int = 1

    # Tenancy
    tenant_id: str = "default"
    workspace_id: str = "default"
    actor_id: str = ""

    # Causal chain
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: Optional[str] = None
    trace_id: Optional[str] = None
    trace_context: Dict[str, str] = Field(default_factory=dict)

    # Coordination
    temporal: TemporalSemantics = Field(default_factory=TemporalSemantics)
    locality: LocalityHint = Field(default_factory=LocalityHint)

    # Fabric metadata (carried losslessly across process boundaries)
    identity: Optional[IdentityContext] = None
    trust: Optional[TrustContext] = None
    provenance: Optional[ProvenanceChain] = None
    activation: Optional[AgentActivationContract] = None
    placement: Optional[PlacementContract] = None

    # QoS
    priority: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Payload
    payload: T

    model_config = {"arbitrary_types_allowed": True}

    def stream_key(self) -> str:
        """Stream key for routing this event."""
        return f"events:{self.event_type}"

    def to_runtime_envelope(self) -> "Envelope":
        """Convert this wire envelope into an in-runtime ``Envelope``.

        Requires ``payload`` to be a ``list[ContentBlock]``; raises ``TypeError``
        otherwise. The payload of a non-content wire event has no in-process
        routing equivalent — keep it as a typed event.
        """
        from ravi.kernel.runtime._contracts import Envelope

        if not isinstance(self.payload, list):
            raise TypeError(
                f"EventEnvelope.to_runtime_envelope requires payload to be a "
                f"list[ContentBlock]; got {type(self.payload).__name__}"
            )

        return Envelope(
            sender=None,
            target=None,  # caller must set target before dispatch
            content=self.payload,
            event_id=self.event_id,
            event_type=self.event_type,
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
        )
