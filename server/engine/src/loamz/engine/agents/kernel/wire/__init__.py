"""The engine↔UI wire protocol — single source of truth.

`events` holds the engine→UI SSE event union (`WireEvent`); `requests` holds the
client→engine bodies. `version.PROTOCOL_VERSION` is asserted by the UI at stream
start. `export` dumps the JSON Schema the UI's TypeScript types are generated from.
"""

from __future__ import annotations

from loamz.engine.agents.kernel.wire.events import (
    ApprovalRequestedEvent,
    Attachment,
    ErrorEvent,
    HandoffEvent,
    HelloEvent,
    InputRequestedEvent,
    PingEvent,
    ReasoningDeltaEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolCallSummary,
    ToolResultEvent,
    TurnCompletedEvent,
    UIResourceEvent,
    WireEvent,
)
from loamz.engine.agents.kernel.wire.from_log import STREAMING_KINDS, wire_from_log
from loamz.engine.agents.kernel.wire.requests import (
    ApprovalResponse,
    CancelRequest,
    ChatRequest,
    InputResponse,
)
from loamz.engine.agents.kernel.wire.version import PROTOCOL_VERSION

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
