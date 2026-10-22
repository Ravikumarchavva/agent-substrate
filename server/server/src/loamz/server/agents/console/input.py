"""Interactive prompt — prompt_toolkit session with graceful fallbacks.

Provides slash-command autocomplete when prompt_toolkit is available, and
degrades to ``RichConsole.input`` / builtin ``input`` for non-TTY environments.
"""

from __future__ import annotations

from io import UnsupportedOperation
from typing import Any

from rich.console import Console as RichConsole

try:
    from prompt_toolkit.completion import Completer, Completion

    class _SlashCompleter(Completer):
        """Complete ``/command`` names at the start of the line."""

        def __init__(self, commands: list[str]) -> None:
            self.commands = commands

        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor
            if text.startswith("/") and " " not in text:
                for cmd in self.commands:
                    if cmd.lower().startswith(text.lower()):
                        yield Completion(cmd, start_position=-len(text))

except ImportError:  # pragma: no cover - prompt_toolkit always installed in practice
    _SlashCompleter = None  # type: ignore[assignment,misc]


class PromptSession:
    """Async input source for the REPL with autocomplete + fallbacks."""

    def __init__(self, console: RichConsole, commands: list[str]) -> None:
        self.console = console
        self.commands = commands
        self._pt_session: Any | None = None

    async def ask(self) -> str:
        """Prompt for one line of input."""
        try:
            return await self._ask_prompt_toolkit()
        except Exception:
            try:
                return self.console.input("\n[user]👤 You → [/user]")
            except UnsupportedOperation:
                return input("\nYou → ")

    async def _ask_prompt_toolkit(self) -> str:
        if _SlashCompleter is None:
            raise ImportError("prompt_toolkit is not available")

        from prompt_toolkit import PromptSession as _PTSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.styles import Style

        if self._pt_session is None:
            style = Style.from_dict(
                {
                    "prompt": "fg:#4e9a06 bold",
                    "completion-menu.completion": "bg:#2c2c2c fg:#cccccc",
                    "completion-menu.completion.current": "bg:#00a0a0 fg:#ffffff bold",
                    "scrollbar.background": "bg:#1e1e1e",
                    "scrollbar.button": "bg:#00a0a0",
                }
            )
            self._pt_session = _PTSession(
                completer=_SlashCompleter(self.commands),
                style=style,
                complete_while_typing=True,
            )

        self.console.print()  # leading newline
        return await self._pt_session.prompt_async(HTML("<prompt>👤 You → </prompt>"))
