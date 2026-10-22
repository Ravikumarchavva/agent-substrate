"""Tool-call cards — compact one-line rows for each tool invocation.

Renders ``⏺ tool_name   ⠿/✓/✗   0.2s`` rows, Claude-Code style, with a live
spinner while a call is in flight.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.console import Group, RenderableType
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .theme import ConsoleTheme


@dataclass
class ToolCall:
    """One tool invocation tracked across its TOOL_CALL → TOOL_RESULT lifecycle."""

    name: str
    agent_key: str = ""
    depth: int = 0
    status: str = "running"  # "running" | "ok" | "error"
    started_at: float = field(default_factory=time.monotonic)
    duration: float | None = None

    def finish(self, *, is_error: bool) -> None:
        self.status = "error" if is_error else "ok"
        self.duration = time.monotonic() - self.started_at


def _row(call: ToolCall, theme: ConsoleTheme) -> Table:
    """Build a single borderless row for one tool call."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(no_wrap=True)  # indent
    grid.add_column(no_wrap=True)  # bullet / spinner
    grid.add_column(no_wrap=True)  # name (+ agent tag)
    grid.add_column(no_wrap=True)  # status + duration

    indent = "  " * (call.depth + 1)

    if call.status == "running":
        bullet: RenderableType = Spinner(theme.spinner, style="tool_name")
    elif call.status == "error":
        bullet = Text(theme.err_icon, style="tool_err")
    else:
        bullet = Text(theme.ok_icon, style="tool_ok")

    tag = f"[dim]\\[{call.agent_key}][/dim] " if call.depth > 0 else ""
    name = Text.from_markup(f"{tag}[tool_name]{call.name}[/tool_name]")

    if call.duration is not None:
        meta = Text(f"{call.duration:.1f}s", style="info")
    else:
        meta = Text("running", style="info")

    grid.add_row(indent, bullet, name, meta)
    return grid


def render_tool_rows(calls: list[ToolCall], theme: ConsoleTheme) -> RenderableType:
    """Render all tracked tool calls as a stacked group of rows."""
    return Group(*(_row(c, theme) for c in calls))
