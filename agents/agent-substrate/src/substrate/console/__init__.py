"""Interactive console for running agents in CLI and notebooks.

A modular, rich, streaming REPL: live assistant/reasoning panels, tool-call
cards, a subagent progress tree, a status line, and a swappable theme.

Usage (single task)::

    async with Runtime() as rt:
        await rt.register(agent)
        result = await Console(agent, runtime=rt).run("What is 2+2?")

Usage (interactive REPL)::

    async with Runtime() as rt:
        await rt.register(agent)
        await Console(agent, runtime=rt).interactive()

Usage (stream watcher — wrap any async iterator)::

    async for _ in Console.stream(some_async_iter):
        pass
"""

from __future__ import annotations

from .app import Console
from .hitl import ConsoleHumanHandler
from .theme import ConsoleTheme, DEFAULT_THEME

__all__ = ["Console", "ConsoleHumanHandler", "ConsoleTheme", "DEFAULT_THEME"]
