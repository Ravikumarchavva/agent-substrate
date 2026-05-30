"""Runtime contracts — the message protocols agents talk over.

A thin, in-process core. Agent↔agent is synchronous full request/response;
the only chunked path is the user-facing visibility stream (:mod:`._stream`).
:class:`AgentRuntime` is the seam a future distributed backend implements.
"""

from __future__ import annotations

from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._message import (
    Envelope,
    MessageContext,
    MessageHandler,
    RuntimeRef,
    Subscription,
)
from ravi.kernel.runtime._protocol import AgentRuntime
from ravi.kernel.runtime._errors import AgentNotFoundError, HandlerError
from ravi.kernel.runtime._stream import StreamDone, StreamPublisher

__all__ = [
    # Routing keys
    "AgentId",
    "TopicId",
    # Protocol
    "AgentRuntime",
    # Message contracts
    "Envelope",
    "MessageContext",
    "MessageHandler",
    "RuntimeRef",
    "Subscription",
    # Errors
    "AgentNotFoundError",
    "HandlerError",
    # Visibility stream
    "StreamDone",
    "StreamPublisher",
]
