"""Low-level rendering primitives: message panels and text helpers.

Pure rendering — these functions take text + a :class:`ConsoleTheme` and return
Rich renderables. No state, no I/O.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.panel import Panel

from .theme import ConsoleTheme


def has_wide_or_combining_characters(text: str) -> bool:
    """Detect Indic/CJK characters that break terminal cell-width math.

    Such text makes Rich's ``Live`` region miscalculate widths; the caller falls
    back to plain sequential printing when this returns True.
    """
    for char in text:
        cp = ord(char)
        # 0x0900–0x0FFF: Indic / South-East Asian scripts
        # 0x3000–0x9FFF: CJK symbols, kana, CJK ideographs
        if 0x0900 <= cp <= 0x0FFF or 0x3000 <= cp <= 0x9FFF:
            return True
    return False


def user_markup(text: str, theme: ConsoleTheme) -> str:
    """Markup string echoing the user's prompt."""
    return f"[user]{theme.user_icon} You →[/user] {text}"


def assistant_panel(text: str, name: str, theme: ConsoleTheme) -> Panel:
    """Panel rendering the assistant's (markdown) reply."""
    return Panel(
        Markdown(text or ""),
        title=f"[agent]{theme.assistant_icon} {name}[/agent]",
        border_style=theme.border,
        padding=(1, 2),
    )


def reasoning_panel(text: str, name: str, theme: ConsoleTheme) -> Panel:
    """Dim panel rendering the model's reasoning/thinking trace."""
    return Panel(
        text or "",
        title=f"[thinking]{theme.thinking_icon} {name} thinking…[/thinking]",
        border_style=theme.thinking_border,
        padding=(1, 2),
    )


def error_panel(
    message: str, theme: ConsoleTheme, *, title: str = "Run failed"
) -> Panel:
    """Red panel surfacing a failed/blocked/cancelled run."""
    return Panel(
        f"[error]{message}[/error]",
        title=f"[error]{theme.err_icon} {title}[/error]",
        border_style="red",
        padding=(1, 2),
    )
