"""Actor-based agent runtime primitives.

Public API::

    from raavan.core.runtime import (
        # Identity
        AgentId,
        TopicId,
        # Protocol
        AgentRuntime,
        AgentFactory,
        # Base class
        BaseRuntime,
        # Message types
        CancellationToken,
        Envelope,
        MessageContext,
        MessageHandler,
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
        # Default runtime
        LocalRuntime,
    )
"""

from __future__ import annotations

from raavan.core.runtime._protocol import AgentId, TopicId, AgentRuntime, AgentFactory
from raavan.core.runtime._base import BaseRuntime
from raavan.core.runtime._types import (
    CancellationToken,
    Envelope,
    MessageContext,
    MessageHandler,
    Subscription,
    RestartPolicy,
    StreamDone,
)
from raavan.core.runtime._mailbox import Mailbox, MailboxFullError
from raavan.core.runtime._dispatcher import Dispatcher, AgentNotFoundError
from raavan.core.runtime._supervisor import Supervisor, SupervisorEscalation
from raavan.core.runtime._local import HandlerError, LocalRuntime
from raavan.core.runtime._stream import StreamPublisher

__all__ = [
    # Identity
    "AgentId",
    "TopicId",
    # Protocol
    "AgentRuntime",
    "AgentFactory",
    # Base class
    "BaseRuntime",
    # Message types
    "CancellationToken",
    "Envelope",
    "MessageContext",
    "MessageHandler",
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
    # Errors
    "HandlerError",
    # Default runtime
    "LocalRuntime",
]
