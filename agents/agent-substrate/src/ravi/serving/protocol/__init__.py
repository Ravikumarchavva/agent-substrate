"""The engine↔UI wire protocol — single source of truth.

`events` holds the engine→UI SSE event union (`WireEvent`); `requests` holds the
client→engine bodies. `version.PROTOCOL_VERSION` is asserted by the UI at stream
start. `export` dumps the JSON Schema the UI's TypeScript types are generated from.
"""

from __future__ import annotations

from ravi.serving.protocol.version import PROTOCOL_VERSION
from ravi.serving.protocol.events import (
    WireEvent,
    HelloEvent,
    TextDeltaEvent,
    ReasoningDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    HandoffEvent,
    ToolCallSummary,
    Attachment,
    TurnCompletedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunCancelledEvent,
    ApprovalRequestedEvent,
    InputRequestedEvent,
    TaskCreatedEvent,
    TaskUpdatedEvent,
    TaskAddedEvent,
    TaskDeletedEvent,
    ErrorEvent,
    PingEvent,
)
from ravi.serving.protocol.requests import (
    ChatRequest,
    ApprovalResponse,
    InputResponse,
    CancelRequest,
)

__all__ = [
    "PROTOCOL_VERSION",
    # events
    "WireEvent",
    "HelloEvent",
    "TextDeltaEvent",
    "ReasoningDeltaEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "HandoffEvent",
    "ToolCallSummary",
    "Attachment",
    "TurnCompletedEvent",
    "RunCompletedEvent",
    "RunFailedEvent",
    "RunCancelledEvent",
    "ApprovalRequestedEvent",
    "InputRequestedEvent",
    "TaskCreatedEvent",
    "TaskUpdatedEvent",
    "TaskAddedEvent",
    "TaskDeletedEvent",
    "ErrorEvent",
    "PingEvent",
    # requests
    "ChatRequest",
    "ApprovalResponse",
    "InputResponse",
    "CancelRequest",
]
