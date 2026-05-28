"""Core runtime contracts — canonical typed interfaces.

These are the source-of-truth types for the engine. Every subsystem
that exchanges data across a boundary (tool loop, message protocol,
event bus) should use these contracts, not ad-hoc dicts or
provider-shaped classes.

Import from here, not from the private ``_*.py`` submodules.
"""

from __future__ import annotations

from ravi.kernel.contracts._coordination import (
    LocalityHint,
    TemporalSemantics,
    TrustLevel,
    TrustSignal,
    TrustContext,
    PlacementScope,
    DataGravityHint,
    PlacementContract,
)
from ravi.kernel.contracts._event import EventEnvelope
from ravi.kernel.runtime._identity import (
    DelegationToken,
    IdentityContext,
    PrincipalId,
    PrincipalKind,
)
from ravi.kernel.runtime._lifecycle import (
    ActivationAware,
    ActivationTrigger,
    AgentActivationContract,
    AgentLifecycleState,
    Checkpointable,
    CheckpointRef,
    ExecutionLease,
)
from ravi.kernel.events._fabric import (
    AckRequest,
    ConsumeRequest,
    DurableEventLog,
    EventDeliveryMode,
    EventFabric,
    EventPriority,
    PublishRequest,
    RealtimeFanout,
    SubscribeRequest,
)
from ravi.kernel.contracts._message import CanonicalMessage, MessageRole, ToolCallSpec
from ravi.kernel.contracts._tool import ToolCallRequest, ToolExecutionResult
from ravi.kernel.contracts._trust import (
    DelegationProof,
    ProvenanceChain,
    ProvenanceLink,
    PrincipalTrustContext,
    TrustGraph,
    TrustScore,
)

__all__ = [
    # Coordination contracts
    "LocalityHint",
    "TemporalSemantics",
    # Trust (routing / allocation tier)
    "TrustLevel",
    "TrustSignal",
    "TrustContext",
    # Placement contracts
    "PlacementScope",
    "DataGravityHint",
    "PlacementContract",
    # Tool execution
    "ToolCallRequest",
    "ToolExecutionResult",
    # Message protocol
    "CanonicalMessage",
    "MessageRole",
    "ToolCallSpec",
    # Event backbone
    "EventEnvelope",
    # Event fabric contracts
    "EventDeliveryMode",
    "EventPriority",
    "PublishRequest",
    "ConsumeRequest",
    "AckRequest",
    "SubscribeRequest",
    "DurableEventLog",
    "RealtimeFanout",
    "EventFabric",
    # Trust and provenance (principal credentials)
    "TrustScore",
    "TrustGraph",
    "DelegationProof",
    "PrincipalTrustContext",
    "ProvenanceLink",
    "ProvenanceChain",
    # Identity
    "PrincipalId",
    "PrincipalKind",
    "DelegationToken",
    "IdentityContext",
    # Dormant agent lifecycle contracts
    "AgentLifecycleState",
    "ActivationTrigger",
    "ExecutionLease",
    "CheckpointRef",
    "AgentActivationContract",
    "Checkpointable",
    "ActivationAware",
]
