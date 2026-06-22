"""Console facade — the public ``Console`` class.

Thin orchestrator that wires the modular pieces (theme, stream adapter, live
renderer, status line, input, commands) behind the original API:
``run`` / ``run_stream`` / ``interactive`` / ``stream``.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from rich.console import Console as RichConsole

from substrate.kernel.core.content import content_blocks_to_str
from substrate.kernel.messaging.stream import (
    CompletionEvent,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)
from substrate.logger import setup_logging

from .commands import SlashCommands
from .input import PromptSession
from .live import _FAIL_TITLES, LiveTurn
from .status import StatusLine, model_name
from .stream_adapter import _RunFailed, stream_events
from .taskboard import _TaskBoardUpdate, render_task_board
from .theme import DEFAULT_THEME, ConsoleTheme
from .widgets import assistant_panel, error_panel, user_markup

if TYPE_CHECKING:
    from substrate.agents.runtime import Runtime
    from substrate.capabilities.tools.skills._manager import SkillManager


class Console:
    """Rich interactive console for agent execution via the durable Runtime.

    Parameters
    ----------
    agent:
        A ``ReActAgent``, ``OrchestratorAgent``, or any agent registered with ``runtime``.
    runtime:
        A started ``Runtime`` instance. The agent must already be registered.
    output:
        Optional ``RichConsole`` instance. Created automatically if *None*.
    skill_manager:
        Optional ``SkillManager`` for displaying and tracking skill activations.
    theme:
        Optional :class:`ConsoleTheme` to restyle the console.
    """

    def __init__(
        self,
        agent: Any,
        *,
        runtime: "Runtime",
        output: Optional[RichConsole] = None,
        skill_manager: Optional["SkillManager"] = None,
        theme: ConsoleTheme = DEFAULT_THEME,
    ) -> None:
        self.agent = agent
        self.theme = theme
        self._runtime = runtime
        self.console = output or RichConsole(theme=theme.rich_theme(), highlight=False)
        self._skill_manager = skill_manager
        self._correlation_id = uuid.uuid4().hex
        self._session_skills_used: set[str] = set()
        self._commands = SlashCommands()

        setup_logging(mode="pretty", level=logging.WARNING)

    # ── accessors used by commands ────────────────────────────────────────
    @property
    def name(self) -> str:
        return getattr(self.agent, "name", "Agent")

    def get_tools(self) -> list[Any]:
        tools = getattr(self.agent, "tools", None)
        if tools is None:
            return []
        if hasattr(tools, "all"):
            return tools.all()
        return list(tools)

    def get_agent_skills(self) -> list[Any]:
        return list(getattr(self.agent, "_skills", []))

    # ── single-shot (non-streaming) run ───────────────────────────────────
    async def run(self, task: str, *, _echo: bool = True) -> str:
        """Submit *task*, wait for completion, pretty-print the final result."""
        if _echo:
            self.console.print(f"\n{user_markup(task, self.theme)}")
        status = StatusLine(model=model_name(self.agent))
        final_text = ""
        failed = False

        async for ev in self._events(task):
            if isinstance(ev, CompletionEvent):
                final_text = content_blocks_to_str(ev.content)  # type: ignore[arg-type]
            elif isinstance(ev, _TaskBoardUpdate):
                for board in ev.boards:
                    panel = render_task_board(board, self.theme)
                    if panel is not None:
                        self.console.print(panel)
            elif isinstance(ev, _RunFailed):
                failed = True
                title = _FAIL_TITLES.get(ev.status, "Run failed")
                self.console.print(error_panel(ev.message, self.theme, title=title))
            elif isinstance(ev, StreamDone):
                break

        if final_text and not failed:
            self.console.print()
            self.console.print(assistant_panel(final_text, self.name, self.theme))
        self.console.print(status.render(self.theme, done=True, failed=failed))
        return final_text

    # ── streaming run ─────────────────────────────────────────────────────
    async def run_stream(self, task: str, *, _echo: bool = True) -> str:
        """Submit *task* and render streamed output live as it arrives."""
        if _echo:
            self.console.print(f"\n{user_markup(task, self.theme)}")
        status = StatusLine(model=model_name(self.agent))
        turn = LiveTurn(self.console, name=self.name, theme=self.theme, status=status)
        final = await turn.consume(self._events(task))
        self.console.print(status.render(self.theme, done=True, failed=turn.failed))
        return final

    def _events(self, task: str) -> AsyncIterator[Any]:
        return stream_events(
            self._runtime, self.agent, task, correlation_id=self._correlation_id
        )

    # ── static stream watcher ─────────────────────────────────────────────
    @staticmethod
    async def stream(
        iterator: AsyncIterator[Any],
        *,
        output: Optional[RichConsole] = None,
    ) -> AsyncIterator[Any]:
        """Wrap any async iterator of stream events with pretty inline printing."""
        con = output or RichConsole(theme=DEFAULT_THEME.rich_theme(), highlight=False)
        partial = ""
        async for chunk in iterator:
            if isinstance(chunk, TextDelta):
                partial += chunk.text
                con.print(chunk.text, end="", style="")
                _flush(con)
            elif isinstance(chunk, ReasoningDelta):
                con.print(chunk.text, end="", style="thinking")
                _flush(con)
            elif isinstance(chunk, CompletionEvent):
                if partial:
                    con.print()
                    partial = ""
            yield chunk

    # ── interactive REPL ──────────────────────────────────────────────────
    async def interactive(
        self,
        *,
        greeting: Optional[str] = None,
        stream: bool = True,
    ) -> None:
        """Run an interactive chat loop. Commands: /tools /skills /reset /help /q."""
        self.console.print(self._greeting(greeting))
        prompt = PromptSession(self.console, self._commands.names)

        while True:
            try:
                user_input = await prompt.ask()
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n👋 Bye!", style="info")
                break

            stripped = user_input.strip()
            if not stripped:
                continue
            if self._commands.matches(stripped):
                if await self._commands.handle(stripped, self):
                    break
                continue

            before = self._active_skills()
            try:
                if stream:
                    await self.run_stream(stripped, _echo=False)
                else:
                    await self.run(stripped, _echo=False)
            except Exception as exc:
                self.console.print(f"[error]Error: {exc}[/error]")
            self._report_new_skills(before)

    # ── greeting + skill tracking ─────────────────────────────────────────
    def _greeting(self, greeting: Optional[str]):
        from rich.panel import Panel

        if greeting is not None:
            return Panel(greeting, border_style=self.theme.border, padding=(0, 1))

        tool_count = len(self.get_tools())
        if self._skill_manager:
            skill_count = len(self._skill_manager.available_names)
            active_count = len(self._skill_manager._active)
        else:
            skills = self.get_agent_skills()
            skill_count = active_count = len(skills)

        skill_summary = (
            f"[bold]{skill_count} skills available[/bold]"
            if skill_count
            else "no skills"
        )
        if active_count:
            skill_summary += f" ([skill]{active_count} active[/skill])"

        text = (
            f"[agent]{self.name}[/agent] ready · "
            f"[bold]{tool_count} tools[/bold] · {skill_summary}\n"
            f"  [dim]/tools · /skills · /model · /reset · /help · /q[/dim]"
        )
        return Panel(text, border_style=self.theme.border, padding=(0, 1))

    def _active_skills(self) -> set[str]:
        if self._skill_manager:
            return set(self._skill_manager._active.keys())
        return set()

    def _report_new_skills(self, before: set[str]) -> None:
        newly = self._active_skills() - before
        if newly:
            self._session_skills_used.update(newly)
            names = ", ".join(sorted(newly))
            self.console.print(
                f"  [skill]⚡ Skill activated: {names}[/skill]", style="bold"
            )


def _flush(con: RichConsole) -> None:
    if hasattr(con.file, "flush"):
        con.file.flush()
