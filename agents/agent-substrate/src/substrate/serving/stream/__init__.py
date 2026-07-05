"""Engine streaming glue: the run session that turns a run into wire events."""

from __future__ import annotations

from substrate.serving.stream.history import project_thread
from substrate.serving.stream.session import (
    AgentStreamSession,
    tail_wire_events,
)

__all__ = [
    "AgentStreamSession",
    "tail_wire_events",
    "project_thread",
]
