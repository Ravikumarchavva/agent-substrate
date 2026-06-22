"""Centralized console theme — styles, icons, and symbols in one swappable place.

Everything that controls how the console *looks* lives here so the rest of the
package never hard-codes a colour or a glyph. Swap ``DEFAULT_THEME`` (or pass a
custom :class:`ConsoleTheme`) to restyle the whole CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.theme import Theme

# Style tokens referenced via Rich markup ([agent], [tool_ok], …) across the
# package. Names are kept stable so markup strings stay valid everywhere.
_STYLES: dict[str, str] = {
    "agent": "bold cyan",
    "user": "bold green",
    "tool_name": "bold yellow",
    "tool_ok": "green",
    "tool_err": "red",
    "thinking": "dim italic",
    "skill": "bold magenta",
    "skill_active": "magenta",
    "skill_dim": "dim magenta",
    "info": "dim",
    "error": "bold red",
    "subagent": "cyan",
}

# Kanban task-status icon + style, and the order tasks are grouped in.
_STATUS_ICONS: dict[str, tuple[str, str]] = {
    "planned": ("○", "dim"),
    "in_progress": ("⟳", "yellow"),
    "blocked": ("⏸", "dark_orange"),
    "succeeded": ("✔", "green"),
    "failed": ("✖", "red"),
    "abandoned": ("⚫", "dim"),
}

_STATUS_ORDER: list[str] = [
    "planned",
    "in_progress",
    "blocked",
    "failed",
    "abandoned",
    "succeeded",
]


@dataclass(frozen=True)
class ConsoleTheme:
    """A complete look-and-feel for the console.

    Holds Rich style tokens plus the glyphs used by widgets. Construct a variant
    and pass it to :class:`~substrate.console.app.Console` to restyle everything.
    """

    styles: dict[str, str] = field(default_factory=lambda: dict(_STYLES))
    status_icons: dict[str, tuple[str, str]] = field(
        default_factory=lambda: dict(_STATUS_ICONS)
    )
    status_order: list[str] = field(default_factory=lambda: list(_STATUS_ORDER))

    # Borders
    border: str = "cyan"
    thinking_border: str = "dim"

    # Glyphs
    assistant_icon: str = "🤖"
    user_icon: str = "👤"
    thinking_icon: str = "💭"
    tool_bullet: str = "⏺"
    ok_icon: str = "✔"
    err_icon: str = "✖"
    handoff_icon: str = "↳"
    gutter_char: str = "┃"
    thinking_gutter: str = "│"
    spinner: str = "dots"  # any rich.spinner name

    def rich_theme(self) -> Theme:
        """Build the Rich ``Theme`` consumed by ``RichConsole(theme=…)``."""
        return Theme(self.styles)


DEFAULT_THEME = ConsoleTheme()
