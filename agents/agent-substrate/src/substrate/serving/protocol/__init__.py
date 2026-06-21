"""The engine↔UI wire protocol — single source of truth.

`events` holds the engine→UI SSE event union (`WireEvent`); `requests` holds the
client→engine bodies. `version.PROTOCOL_VERSION` is asserted by the UI at stream
start. `export` dumps the JSON Schema the UI's TypeScript types are generated from.
"""

from __future__ import annotations

from substrate.serving.protocol.version import PROTOCOL_VERSION
from substrate.serving.protocol.events import (
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
    UIResourceEvent,
    ErrorEvent,
    PingEvent,
)
from substrate.serving.protocol.requests import (
    ChatRequest,
    ApprovalResponse,
    InputResponse,
    CancelRequest,
)
from substrate.serving.protocol.from_log import wire_from_log, STREAMING_KINDS

__all__ = [
    "PROTOCOL_VERSION",
    "wire_from_log",
    "STREAMING_KINDS",
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
    "UIResourceEvent",
    "ErrorEvent",
    "PingEvent",
    # requests
    "ChatRequest",
    "ApprovalResponse",
    "InputResponse",
    "CancelRequest",
]
