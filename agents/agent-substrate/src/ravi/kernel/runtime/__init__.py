from __future__ import annotations

# Identity value-objects
from ravi.kernel.runtime._identity import (
    AgentId,
    DelegationToken,
    IdentityContext,
    PrincipalId,
    PrincipalKind,
    TopicId,
)

# Protocol
from ravi.kernel.runtime._protocol import AgentRuntime

# Contracts (typed data structures)
from ravi.kernel.runtime._contracts import (
    CancellationToken,
    Envelope,
    MessageContext,
    MessageHandler,
    RestartPolicy,
    RuntimeRef,
    StreamDone,
    Subscription,
)

# Errors (single source of truth)
from ravi.kernel.runtime._errors import (
    AgentNotFoundError,
    CheckpointCorruptedError,
    DeadlockDetectedError,
    EnvelopeExpiredError,
    HandlerError,
    LeaseAcquisitionFailed,
    MailboxFullError,
    ResourceConflictError,
    SagaFailedError,
    SupervisorEscalation,
)

# Backpressure
from ravi.kernel.runtime._backpressure import (
    BackpressureAction,
    BackpressurePolicy,
    BackpressureSignal,
)

# Lease coordination
from ravi.kernel.runtime._lease import (
    DEFAULT_LEASE_TTL_SECONDS,
    InMemoryLeaseRegistry,
    LeaseAcquisitionResult,
    LeaseRegistry,
)

# Routing middleware
from ravi.kernel.runtime._middleware import (
    DropEnvelope,
    RoutingMiddleware,
    RoutingMiddlewareRejection,
)

# Streaming
from ravi.kernel.runtime._stream import StreamPublisher

# Dormant agent lifecycle contracts
from ravi.kernel.runtime._lifecycle import (
    ActivationAware,
    ActivationTrigger,
    AgentActivationContract,
    AgentLifecycleState,
    Checkpointable,
    CheckpointRef,
    ExecutionLease,
)

__all__ = [
    # Identity
    "AgentId",
    "TopicId",
    "PrincipalId",
    "PrincipalKind",
    "DelegationToken",
    "IdentityContext",
    # Protocol
    "AgentRuntime",
    # Contracts
    "CancellationToken",
    "Envelope",
    "MessageContext",
    "MessageHandler",
    "RuntimeRef",
    "Subscription",
    # Streaming
    "StreamDone",
    "StreamPublisher",
    # Supervisor / Dispatcher
    "RestartPolicy",
    "AgentNotFoundError",
    # Backpressure
    "BackpressureAction",
    "BackpressurePolicy",
    "BackpressureSignal",
    # Lease coordination
    "DEFAULT_LEASE_TTL_SECONDS",
    "InMemoryLeaseRegistry",
    "LeaseAcquisitionFailed",
    "LeaseAcquisitionResult",
    "LeaseRegistry",
    # Routing middleware
    "DropEnvelope",
    "RoutingMiddleware",
    "RoutingMiddlewareRejection",
    # Errors
    "HandlerError",
    "EnvelopeExpiredError",
    "CheckpointCorruptedError",
    "DeadlockDetectedError",
    "LeaseAcquisitionFailed",
    "MailboxFullError",
    "ResourceConflictError",
    "SagaFailedError",
    "SupervisorEscalation",
    # Dormant agent lifecycle contracts
    "AgentLifecycleState",
    "ActivationTrigger",
    "ExecutionLease",
    "CheckpointRef",
    "AgentActivationContract",
    "Checkpointable",
    "ActivationAware",
]
