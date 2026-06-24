"""HITL console support — Claude-style human-input picker.

``ConsoleHumanHandler`` is a thin marker that tells ``AskHumanTool`` to
suspend the run via ``ctx.sleep_until_signal()`` instead of blocking on a
Future.  The ``LiveTurn`` renderer sees the ``_HITLRequest`` event (emitted
from the ``input.requested`` log entry), stops the live region, renders an
option card, collects the user's choice, and fires
``SignalBus.signal(run_id, "hitl:<id>", payload)`` to resume the suspended
run.  Zero compute is consumed while the human decides.

Usage::

    from substrate.console.hitl import ConsoleHumanHandler
    from substrate.capabilities.tools.human_input import AskHumanTool

    handler = ConsoleHumanHandler()
    ask = AskHumanTool(handler=handler)

    agent = ReActAgent(name="assistant", model_client=client, tools=[ask])

    async with Runtime() as rt:
        await Console(agent, runtime=rt, hitl_handler=handler).run_stream("help me")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from substrate.capabilities.tools.human_input import InputOption
    from .theme import ConsoleTheme


# ---------------------------------------------------------------------------
# Internal stream event
# ---------------------------------------------------------------------------


@dataclass
class _HITLRequest:
    """Internal event: the agent is paused waiting for human input."""

    request_id: str
    question: str
    context: str
    options: list[InputOption]
    allow_freeform: bool
    run_id: str = ""


# ---------------------------------------------------------------------------
# Handler — signal-mode marker
# ---------------------------------------------------------------------------


class ConsoleHumanHandler:
    """HITL handler marker for the Rich console signal-based path.

    Setting ``suspends_via_signal = True`` tells ``AskHumanTool`` to log
    ``input.requested`` and call ``ctx.sleep_until_signal()`` instead of
    calling ``request_input()``.  The suspended run is resumed when
    ``LiveTurn._handle_hitl`` fires ``SignalBus.signal()`` in response to
    the user's keypress.
    """

    supports_event_log: bool = True
    suspends_via_signal: bool = True


# ---------------------------------------------------------------------------
# Panel renderer
# ---------------------------------------------------------------------------


def render_hitl_panel(ev: _HITLRequest, theme: ConsoleTheme) -> Panel:
    """Render a Claude-style numbered option card."""
    lines = Text()

    if ev.context:
        lines.append(f"  {ev.context}\n", style="dim")
        lines.append("\n")

    lines.append(f"  {ev.question}\n", style="bold")
    lines.append("\n")

    for i, opt in enumerate(ev.options, 1):
        lines.append(f"    {i}  ", style=f"{theme.border} bold")
        lines.append(opt.label)
        if opt.description:
            lines.append(f"  —  {opt.description}", style="dim")
        lines.append("\n")

    if ev.allow_freeform:
        freeform_n = len(ev.options) + 1
        lines.append(f"    {freeform_n}  ", style=f"{theme.border} bold")
        lines.append("Something else", style="dim italic")
        lines.append("  (type your own answer)\n", style="dim")

    lines.append("\n")
    lines.append("  ", style="")
    lines.append("Enter number", style="dim")
    lines.append(" · ", style="dim")
    lines.append("s", style=f"{theme.border} bold")
    lines.append(" to skip", style="dim")
    lines.append(" · ", style="dim")
    lines.append("any other text", style="dim")
    lines.append(" submits as new message", style="dim")

    return Panel(
        lines,
        title="[bold]Human input required[/bold]",
        title_align="left",
        border_style=theme.border,
        padding=(0, 1),
    )
