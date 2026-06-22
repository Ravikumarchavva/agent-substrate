"""Slash-command registry for the interactive REPL.

Keeps ``interactive()`` thin: each ``/command`` maps to a handler that receives
the :class:`~substrate.console.app.Console` facade. Returns ``True`` to quit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Callable

from rich.panel import Panel
from rich.table import Table

from .status import model_name

if TYPE_CHECKING:
    from .app import Console

# A handler receives the Console facade and any trailing argument string.
Handler = Callable[["Console", str], "Awaitable[bool]"]


class SlashCommands:
    """Dispatches ``/cmd [args]`` strings to handlers; extensible via ``register``."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self.register("/tools", _cmd_tools)
        self.register("/skills", _cmd_skills)
        self.register("/model", _cmd_model)
        self.register("/reset", _cmd_reset)
        self.register("/help", _cmd_help)
        self.register("/q", _cmd_quit)

    def register(self, name: str, handler: Handler) -> None:
        self._handlers[name] = handler

    @property
    def names(self) -> list[str]:
        return list(self._handlers)

    def matches(self, text: str) -> bool:
        return self._split(text)[0] in self._handlers

    async def handle(self, text: str, app: "Console") -> bool:
        """Run the matching command; returns True if the session should quit."""
        cmd, args = self._split(text)
        return await self._handlers[cmd](app, args)

    @staticmethod
    def _split(text: str) -> tuple[str, str]:
        head, _, rest = text.strip().partition(" ")
        return head.lower(), rest.strip()


# ── handlers ──────────────────────────────────────────────────────────────
async def _cmd_quit(app: "Console", args: str) -> bool:
    app.console.print("👋 Bye!", style="info")
    return True


async def _cmd_reset(app: "Console", args: str) -> bool:
    agent = app.agent
    if hasattr(agent, "_context") and hasattr(agent._context, "history"):
        await agent._context.history.clear(agent.id, session_id=app._correlation_id)
    app.console.print("🔄 Agent memory cleared.", style="info")
    return False


async def _cmd_help(app: "Console", args: str) -> bool:
    help_text = (
        "[bold]Commands:[/bold]\n"
        "  /tools         — List available tools\n"
        "  /skills        — List discovered skills and active ones\n"
        "  /model [name]  — Show or switch the chat model\n"
        "  /reset         — Clear agent memory\n"
        "  /help          — Show this message\n"
        "  /q             — Quit the session"
    )
    app.console.print(Panel(help_text, border_style="dim", padding=(0, 1)))
    return False


async def _cmd_model(app: "Console", args: str) -> bool:
    name = args.strip()
    if not name:
        app.console.print(
            f"  Current model: [tool_name]{model_name(app.agent)}[/tool_name]"
        )
        app.console.print("  Usage: /model <provider/model-id>", style="info")
        return False

    from substrate.config import SubstrateConfig
    from substrate.integrations.llm import (
        create_model_client,
        detect_provider,
        has_provider_api_key,
    )

    try:
        provider = detect_provider(name)
    except ValueError as exc:
        app.console.print(f"  [error]{exc}[/error]")
        return False

    keys = SubstrateConfig().provider_keys
    if not has_provider_api_key(provider, keys):
        app.console.print(
            f"  [error]No API key for provider {provider!r} — add it to .env.[/error]"
        )
        return False

    try:
        app.agent.model = create_model_client(name, api_keys=keys)
    except Exception as exc:  # noqa: BLE001 - surface any client build failure
        app.console.print(f"  [error]Could not switch model: {exc}[/error]")
        return False

    app.console.print(f"  [tool_ok]✔ Model switched to {name}[/tool_ok]")
    return False


async def _cmd_tools(app: "Console", args: str) -> bool:
    tools = app.get_tools()
    if not tools:
        app.console.print("  No tools registered.", style="info")
        return False
    table = Table(title="Available Tools", show_lines=False, padding=(0, 1))
    table.add_column("Name", style="tool_name")
    table.add_column("Risk", style="dim")
    table.add_column("Description")
    for t in tools:
        name = getattr(t, "name", "?")
        risk = str(getattr(t, "risk", "safe")).split(".")[-1].lower()
        desc = getattr(t, "description", "")
        if len(desc) > 70:
            desc = desc[:67] + "..."
        table.add_row(name, risk, desc)
    app.console.print(table)
    return False


async def _cmd_skills(app: "Console", args: str) -> bool:
    manager = app._skill_manager
    if manager:
        all_meta = manager._loader.all_metadata()
        active_names = set(manager._active.keys())
        if not all_meta:
            app.console.print("  No skills discovered.", style="info")
        else:
            table = Table(title="Skills", show_lines=False, padding=(0, 1))
            table.add_column("Name", style="skill")
            table.add_column("Status", style="dim")
            table.add_column("Description")
            for meta in sorted(all_meta, key=lambda m: m.name):
                status = (
                    "[skill_active]● active[/skill_active]"
                    if meta.name in active_names
                    else "[dim]○ available[/dim]"
                )
                desc = meta.description
                if len(desc) > 65:
                    desc = desc[:62] + "..."
                table.add_row(meta.name, status, desc)
            app.console.print(table)
        if app._session_skills_used:
            names = ", ".join(sorted(app._session_skills_used))
            app.console.print(f"  [skill]Session activated: {names}[/skill]")
        return False

    agent_skills = app.get_agent_skills()
    if not agent_skills:
        app.console.print("  No skills loaded.", style="info")
        return False
    table = Table(title="Active Skills", show_lines=False, padding=(0, 1))
    table.add_column("Name", style="skill")
    table.add_column("Tools", style="dim")
    for s in agent_skills:
        name = getattr(s, "name", "?")
        allowed = ", ".join(getattr(s, "allowed_tools", [])) or "—"
        table.add_row(name, allowed)
    app.console.print(table)
    return False
