"""Generic typed event envelope — canonical cross-service wire format.

``EventEnvelope[T]`` is the single canonical wire format for every event
leaving a runtime process: durable logs, realtime fanout, and replay all
use the same shape.

The in-process ``Envelope`` (``ravi.kernel.runtime._message``) is
deliberately leaner — it keeps only what in-process routing needs.
:meth:`to_runtime_envelope` projects this wire event onto that lean shape.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ravi.kernel.runtime._message import Envelope

T = TypeVar("T")


class EventEnvelope(BaseModel, Generic[T]):
    """Typed domain event envelope — the canonical cross-service wire format.

    ``T`` is the payload type.  Use ``dict[str, Any]`` for unstructured events;
    use a specific Pydantic model for fully-typed domain events.

    All inter-service async events are wrapped in this envelope before being
    published to the durable log or realtime fanout.
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
        """Project this wire event onto the lean in-process ``Envelope``.

        Requires ``payload`` to be a ``list[ContentBlock]``; raises ``TypeError``
        otherwise.
        """
        from ravi.kernel.runtime._message import Envelope

        if not isinstance(self.payload, list):
            raise TypeError(
                f"EventEnvelope.to_runtime_envelope requires payload to be a "
                f"list[ContentBlock]; got {type(self.payload).__name__}"
            )

        return Envelope(
            sender=None,
            target=None,
            content=self.payload,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            trace_id=self.trace_id,
            trace_context=dict(self.trace_context),
            metadata=dict(self.metadata),
        )


__all__ = ["EventEnvelope"]
