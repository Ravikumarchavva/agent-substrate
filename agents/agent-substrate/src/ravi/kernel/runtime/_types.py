"""Backward-compatible re-exports from the refactored runtime modules.

This module exists ONLY so that existing ``from ravi.kernel.runtime._types import ...``
imports continue to work.  New code should import directly from the
canonical modules:

    _identity.py       — AgentId, TopicId
    _contracts.py      — Envelope, MessageContext, MessageHandler, etc.
    _errors.py         — all exception types
    _resource_lock.py  — ResourceLockManager, LockHandle, LockMode
    _client_channel.py — ClientWriteChannel, ClientFrame, WriteLane
    _saga.py           — SagaCoordinator, SagaRecord, SagaStep
    _checkpoint.py     — RunCheckpoint, CheckpointStore, CheckpointStatus
"""

from __future__ import annotations

# Re-export everything that used to live here
from ravi.kernel.runtime._identity import AgentId, TopicId  # noqa: F401
from ravi.kernel.runtime._contracts import (  # noqa: F401
    CancellationToken,
    Envelope,
    MessageContext,
    MessageHandler,
    RestartPolicy,
    StreamDone,
    Subscription,
)

__all__ = [
    "AgentId",
    "TopicId",
    "CancellationToken",
    "Envelope",
    "MessageContext",
    "MessageHandler",
    "RestartPolicy",
    "StreamDone",
    "Subscription",
]
