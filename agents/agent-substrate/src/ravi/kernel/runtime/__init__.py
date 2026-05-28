"""Actor-based agent runtime primitives.

Public API::

    from ravi.kernel.runtime import (
        # Identity
        AgentId,
        TopicId,
        PrincipalId,
        PrincipalKind,
        TrustTier,
        LifecycleState,
        # Protocol
        AgentRuntime,
        # Base class
        BaseRuntime,
        # Contracts
        CancellationToken,
        Envelope,
        MessageContext,
        MessageHandler,
        RuntimeRef,
        Subscription,
        # Streaming
        StreamDone,
        StreamPublisher,
        # Supervisor
        RestartPolicy,
        Supervisor,
        SupervisorEscalation,
        # Dispatcher
        Dispatcher,
        AgentNotFoundError,
        # Mailbox
        Mailbox,
        MailboxFullError,
        # Resource Locking
        ResourceLockManager,
        LockHandle,
        LockMode,
        ResourceConflictError,
        DeadlockDetectedError,
        # Client Channel
        ClientWriteChannel,
        ClientFrame,
        WriteLane,
        # Saga
        SagaCoordinator,
        SagaRecord,
        SagaStep,
        SagaFailedError,
        # Checkpointing
        RunCheckpoint,
        CheckpointStore,
        InMemoryCheckpointStore,
        CheckpointStatus,
        # Default runtime
        LocalRuntime,
        HandlerError,
        # Dormant agent lifecycle contracts
        AgentLifecycleState,
        ActivationTrigger,
        ExecutionLease,
        CheckpointRef,
        AgentActivationContract,
        Checkpointable,
        ActivationAware,
    )
"""

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

# Base runtime
from ravi.kernel.runtime._base import BaseRuntime

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

# Infrastructure
from ravi.kernel.runtime._mailbox import Mailbox
from ravi.kernel.runtime._dispatcher import Dispatcher
from ravi.kernel.runtime._supervisor import Supervisor

# Resource locking
from ravi.kernel.runtime._resource_lock import (
    LockHandle,
    LockMode,
    ResourceLockManager,
)

# Client write channel
from ravi.kernel.runtime._client_channel import (
    ClientFrame,
    ClientWriteChannel,
    WriteLane,
)

# Saga coordinator
from ravi.kernel.runtime._saga import (
    SagaCoordinator,
    SagaRecord,
    SagaStep,
)

# Hierarchical checkpointing
from ravi.kernel.runtime._checkpoint import (
    CheckpointStatus,
    CheckpointStore,
    InMemoryCheckpointStore,
    RunCheckpoint,
)

# Default runtime
from ravi.kernel.runtime._local import LocalRuntime

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

# Streaming
from ravi.kernel.runtime._stream import StreamPublisher

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
    # Base class
    "BaseRuntime",
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
    # Supervisor
    "RestartPolicy",
    "Supervisor",
    "SupervisorEscalation",
    # Dispatcher
    "Dispatcher",
    "AgentNotFoundError",
    # Mailbox
    "Mailbox",
    "MailboxFullError",
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
    # Resource Locking
    "ResourceLockManager",
    "LockHandle",
    "LockMode",
    "ResourceConflictError",
    "DeadlockDetectedError",
    # Client Channel
    "ClientWriteChannel",
    "ClientFrame",
    "WriteLane",
    # Saga
    "SagaCoordinator",
    "SagaRecord",
    "SagaStep",
    "SagaFailedError",
    # Checkpointing
    "RunCheckpoint",
    "CheckpointStore",
    "InMemoryCheckpointStore",
    "CheckpointStatus",
    "CheckpointCorruptedError",
    # Errors
    "HandlerError",
    "EnvelopeExpiredError",
    # Default runtime
    "LocalRuntime",
    # Dormant agent lifecycle contracts
    "AgentLifecycleState",
    "ActivationTrigger",
    "ExecutionLease",
    "CheckpointRef",
    "AgentActivationContract",
    "Checkpointable",
    "ActivationAware",
]
