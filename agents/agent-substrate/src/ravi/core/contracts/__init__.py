"""Core runtime contracts — canonical typed interfaces.

These are the source-of-truth types for the engine. Every subsystem
that exchanges data across a boundary (tool loop, message protocol,
event bus) should use these contracts, not ad-hoc dicts or
provider-shaped classes.

Import from here, not from the private ``_*.py`` submodules.
"""

from __future__ import annotations

from ravi.core.contracts._event import EventEnvelope
from ravi.core.contracts._message import CanonicalMessage, MessageRole, ToolCallSpec
from ravi.core.contracts._tool import ToolCallRequest, ToolExecutionResult

__all__ = [
    # Tool execution
    "ToolCallRequest",
    "ToolExecutionResult",
    # Message protocol
    "CanonicalMessage",
    "MessageRole",
    "ToolCallSpec",
    # Event backbone
    "EventEnvelope",
]
