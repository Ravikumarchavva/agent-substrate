"""Actor-based agent runtime primitives.

Public API::

    from ravi.core.runtime import (
        # Identity
        AgentId,
        TopicId,
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
    )
"""

from __future__ import annotations

# Identity value-objects
from ravi.core.runtime._identity import AgentId, TopicId

# Protocol
from ravi.core.runtime._protocol import AgentRuntime

# Base runtime
from ravi.core.runtime._base import BaseRuntime

# Contracts (typed data structures)
from ravi.core.runtime._contracts import (
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
from ravi.core.runtime._errors import (
    AgentNotFoundError,
    CheckpointCorruptedError,
    DeadlockDetectedError,
    EnvelopeExpiredError,
    HandlerError,
    MailboxFullError,
    ResourceConflictError,
    SagaFailedError,
    SupervisorEscalation,
)

# Infrastructure
from ravi.core.runtime._mailbox import Mailbox
from ravi.core.runtime._dispatcher import Dispatcher
from ravi.core.runtime._supervisor import Supervisor

# Resource locking
from ravi.core.runtime._resource_lock import (
    LockHandle,
    LockMode,
    ResourceLockManager,
)

# Client write channel
from ravi.core.runtime._client_channel import (
    ClientFrame,
    ClientWriteChannel,
    WriteLane,
)

# Saga coordinator
from ravi.core.runtime._saga import (
    SagaCoordinator,
    SagaRecord,
    SagaStep,
)

# Hierarchical checkpointing
from ravi.core.runtime._checkpoint import (
    CheckpointStatus,
    CheckpointStore,
    InMemoryCheckpointStore,
    RunCheckpoint,
)

# Default runtime
from ravi.core.runtime._local import LocalRuntime

# Streaming
from ravi.core.runtime._stream import StreamPublisher

__all__ = [
    # Identity
    "AgentId",
    "TopicId",
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
]
