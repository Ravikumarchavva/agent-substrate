"""Kanban task-board rendering for the ``manage_tasks`` tool.

``_TaskBoardUpdate`` is the internal stream event carrying a board snapshot;
``render_task_board`` turns one board into a Rich panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from rich.panel import Panel
from rich.table import Table

from .theme import ConsoleTheme


@dataclass
class _TaskBoardUpdate:
    """Internal event: task-board snapshot after a ``manage_tasks`` tool call."""

    boards: List[Any] = field(default_factory=list)


def render_task_board(board: Any, theme: ConsoleTheme) -> Panel | None:
    """Render one agent's Kanban board, or ``None`` when it has no tasks."""
    label = (
        getattr(board, "agent_label", "") or getattr(board, "agent_id", "") or "Agent"
    )
    tasks = getattr(board, "tasks", [])
    max_retries = getattr(board, "max_retries", 3)
    total = len(tasks)
    if total == 0:
        return None
    done = sum(1 for t in tasks if getattr(t, "status", "") == "succeeded")

    order = theme.status_order
    sorted_tasks = sorted(
        tasks,
        key=lambda t: (
            order.index(t.status) if t.status in order else 99,
            t.order,
        ),
    )

    table = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    table.add_column("icon", width=2, no_wrap=True)
    table.add_column("title")
    table.add_column("status", width=13, no_wrap=True, style="dim")
    table.add_column("note", style="dim", no_wrap=True)

    for task in sorted_tasks:
        icon, style = theme.status_icons.get(task.status, ("?", "dim"))
        note = task.note or ""
        retry_count = getattr(task, "retry_count", 0)
        if retry_count > 0:
            retry_str = f"retry {retry_count}/{max_retries}"
            note = f"{retry_str} — {note}" if note else retry_str
        table.add_row(
            f"[{style}]{icon}[/{style}]",
            task.title,
            task.status.replace("_", " "),
            note,
        )

    return Panel(
        table,
        title=f"[bold]Tasks · {label}[/bold]  [dim]{done}/{total}[/dim]",
        border_style=theme.border,
        padding=(0, 1),
    )
