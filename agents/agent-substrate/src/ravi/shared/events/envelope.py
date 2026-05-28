"""Standard event envelope for the domain event backbone.

Thin subclass of :class:`ravi.kernel.contracts.EventEnvelope[Dict[str, Any]]`
that fixes the payload type to a JSON-shaped ``Dict[str, Any]`` and supplies
a default. All inter-service async events published to the Redis Streams
backbone use this shape on the wire.

The kernel envelope is the source of truth — this subclass exists only to
preserve the no-payload-required ergonomics that the existing
``shared.events.types`` factories rely on, and to keep the
``identity_context`` JSON sidecar that legacy services still emit.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from ravi.kernel.contracts._event import EventEnvelope as _KernelEventEnvelope


class EventEnvelope(_KernelEventEnvelope[Dict[str, Any]]):
    """Domain event envelope with a ``Dict[str, Any]`` payload default.

    Inherits every fabric field from the kernel envelope (identity, trust,
    provenance, activation, placement, temporal, locality, trace context).
    Adds the legacy ``identity_context`` JSON dict for backward compat with
    services that have not yet migrated to the typed ``identity`` field.
    """

    payload: Dict[str, Any] = Field(default_factory=dict)
    identity_context: Optional[Dict[str, str]] = None
