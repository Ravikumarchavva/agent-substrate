"""Engine streaming glue: the run session that turns a run into wire events."""

from __future__ import annotations

from substrate_cloud.stream.history import (
    append_mcp_app_context,
    append_user_message,
    project_thread,
    project_thread_timed,
)
from substrate_cloud.stream.session import (
    AgentStreamSession,
    sse_lines,
    tail_wire_events,
)

__all__ = [
    "AgentStreamSession",
    "sse_lines",
    "tail_wire_events",
    "project_thread",
    "project_thread_timed",
    "append_mcp_app_context",
    "append_user_message",
]
