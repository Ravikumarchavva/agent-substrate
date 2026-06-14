"""Interactive console for running agents in CLI and notebooks.

Inspired by AutoGen's ``Console`` — provides a rich, formatted view of agent
execution including streaming text, tool calls, reasoning traces, and results.

Shows discovered skills at startup, tracks which skills are active, and
lists activated skills in the session footer.

Usage (single task)::

    from ravi.console import Console

    result = await Console(agent).run("What is 2+2?")

Usage (interactive REPL with skills)::

    from ravi.capabilities.tools.skills._manager import SkillManager
    manager = SkillManager()
    await Console(agent, skill_manager=manager).interactive(stream=True)

Usage (stream watcher — attach to any ``run_stream`` iterator)::

    async for _ in Console.stream(agent.run_stream("Hello")):
        pass
"""

from __future__ import annotations

import asyncio
import logging
from io import UnsupportedOperation
import json
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, List

from rich.console import Console as RichConsole, Group, ConsoleOptions, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme
from rich.segment import Segment

from ravi.kernel.messaging.stream import (
    TextDelta,
    ReasoningDelta,
    CompletionEvent,
    AgentProgress,
    AgentStep,
)
from ravi.kernel.tools import ToolExecutionResult
from ravi.logger import setup_logging

if TYPE_CHECKING:
    from ravi.capabilities.tools.skills._manager import SkillManager


class GutterAccent:
    """A custom Rich renderable that prepends a vertical gutter line to every line of content."""

    def __init__(
        self, renderable: Any, line_char: str = "┃", style: str = "bold cyan"
    ) -> None:
        self.renderable = renderable
        self.line_char = line_char
        self.style = style

    def __rich_console__(
        self, console: RichConsole, options: ConsoleOptions
    ) -> RenderResult:
        # Gutter string: line character plus 2 spaces (width 3)
        gutter_str = f"{self.line_char}  "
        # Update width to account for the 3-character gutter
        child_options = options.update_width(max(1, options.max_width - 3))
        segments = console.render(self.renderable, child_options)

        gutter = Segment(gutter_str, console.get_style(self.style))
        newline = Segment("\n")

        for line in Segment.split_lines(segments):
            yield gutter
            yield from line
            yield newline


def _build_accent_layout(
    name: str, content_markdown: Markdown, status: Optional[str] = None
) -> Group:
    """Build a timeline-based layout with an agent header and left vertical accent line."""
    status_str = f" [dim]({status})[/dim]" if status else ""
    header = f"   [agent]🤖 {name}[/agent]{status_str}"
    accented_content = GutterAccent(content_markdown, line_char="┃", style="bold cyan")
    return Group(header, accented_content)


def _build_thinking_layout(
    name: str, content: str, status: Optional[str] = None
) -> Group:
    """Build a layout for thinking/reasoning chunks with a dim vertical gutter."""
    status_str = f" [dim]({status})[/dim]" if status else ""
    header = f"   [thinking]💭 {name} thinking...[/thinking]{status_str}"
    accented_content = GutterAccent(content, line_char="│", style="thinking")
    return Group(header, accented_content)


# ---------------------------------------------------------------------------
# Streaming channel for actor-model agents
# ---------------------------------------------------------------------------


class _ConsoleStreamChannel:
    """StreamChannel adapter that exposes chunks as an async iterator.

    Bridges the push-based StreamChannel protocol (used by ActorAgent) to the
    pull-based iteration the Console rendering loop expects.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._done = object()

    async def emit(self, chunk: object) -> None:
        await self._queue.put(chunk)

    def close(self) -> None:
        self._queue.put_nowait(self._done)

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        while True:
            item = await self._queue.get()
            if item is self._done:
                return
            yield item  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_THEME = Theme(
    {
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
    }
)


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------


class Console:
    """Rich interactive console for agent execution.

    Works with both actor-model agents (``ActorAgent`` subclasses) and
    legacy callable agents (``BaseAgent`` subclasses).

    For actor-model agents, a ``UserProxyAgent`` is created automatically
    using the agent's own runtime.  No extra setup required.

    Parameters
    ----------
    agent:
        An ``ActorAgent`` (new) or a legacy ``BaseAgent`` / ``ReActAgent``.
    output:
        Optional ``RichConsole`` instance. Created automatically if *None*.
    """

    def __init__(
        self,
        agent: Any,
        *,
        output: Optional[RichConsole] = None,
        skill_manager: Optional["SkillManager"] = None,
        runtime: Any = None,
    ) -> None:
        import uuid as _uuid

        self.agent = agent
        self.console = output or RichConsole(theme=_THEME, highlight=False)
        self._proxy: Optional[Any] = None
        self._skill_manager = skill_manager
        self._runtime = runtime  # Runtime; if set, use RunStreamAdapter
        self._correlation_id = _uuid.uuid4().hex
        # Snapshot of active skills at session start (to detect newly activated ones)
        self._session_skills_used: set[str] = set()

        # Configure logging for interactive use (once per process)
        setup_logging(mode="pretty", level=logging.WARNING)

    def _is_actor_agent(self) -> bool:
        """Return True if the agent uses the actor model (has on_message)."""
        return False

    async def _get_proxy(self) -> Any:
        """Lazily create and start the UserProxyAgent for actor-model agents."""
        if self._proxy is None:
            from ravi.agents.core import UserProxyAgent

            self._proxy = UserProxyAgent(
                "console-proxy",
                self.agent.runtime,
                key=f"console-{id(self):x}",
            )
            await self._proxy.start()
        return self._proxy

    async def _run_durable(self, task: str) -> str:
        """Run via RunStreamAdapter; accumulate and return the final text."""
        from ravi.serving.stream.run_adapter import RunStreamAdapter
        from ravi.kernel.messaging.stream import CompletionEvent, StreamDone

        adapter = RunStreamAdapter(
            agent_id=self.agent.id,
            runtime=self._runtime,
            tools=list(self.agent.tools.all()) if self.agent.tools else [],
            correlation_id=self._correlation_id,
        )
        final_text = ""
        async for chunk in adapter._stream(task):
            if isinstance(chunk, CompletionEvent):
                from ravi.kernel.core.content import content_blocks_to_str
                final_text = content_blocks_to_str(chunk.content)  # type: ignore[arg-type]
            elif isinstance(chunk, StreamDone):
                break
        return final_text

    # ------------------------------------------------------------------
    # Single-shot run (non-streaming)
    # ------------------------------------------------------------------

    async def run(self, task: str, *, _echo: bool = True, **kwargs: Any) -> Any:
        """Run the agent on *task* and pretty-print the result.

        Returns the ``AgentRunResult``.
        """
        if _echo:
            self._print_user(task)
        t0 = time.monotonic()

        if self._runtime is not None:
            # Durable: submit + wait for run.completed via streaming (non-streaming is just full accumulation)
            result = await self._run_durable(task)
        elif self._is_actor_agent():
            proxy = await self._get_proxy()
            result = await proxy.ask(task, recipient=self.agent.id)
        else:
            result = await self.agent.run(task, **kwargs)

        elapsed = time.monotonic() - t0
        self._print_result(result, elapsed)
        return result

    # ------------------------------------------------------------------
    # Streaming run
    # ------------------------------------------------------------------

    async def run_stream(self, task: str, *, _echo: bool = True, **kwargs: Any) -> Any:
        """Run the agent with streaming and pretty-print each chunk.

        Returns the final ``CompletionChunk.message`` (or *None*).
        """

        if _echo:
            self._print_user(task)
        t0 = time.monotonic()

        text_buffer: List[str] = []
        reasoning_buffer: List[str] = []
        final_message: Any = None
        tool_calls_count = 0

        if self._runtime is not None:
            from ravi.serving.stream.run_adapter import RunStreamAdapter
            adapter = RunStreamAdapter(
                agent_id=self.agent.id,
                runtime=self._runtime,
                tools=list(self.agent.tools.all()) if self.agent.tools else [],
                correlation_id=self._correlation_id,
            )
            chunk_iter: AsyncIterator[Any] = adapter._stream(task)
        elif self._is_actor_agent():
            proxy = await self._get_proxy()
            channel = _ConsoleStreamChannel()
            # ask_stream() returns as soon as on_message() spawns the background task.
            await proxy.ask_stream(task, recipient=self.agent.id, channel=channel)
            chunk_iter = channel.__aiter__()
        else:
            chunk_iter = self.agent.run_stream(task, **kwargs)

        live = None
        text_stream_active = False
        reasoning_stream_active = False
        text_updated = asyncio.Event()
        reasoning_updated = asyncio.Event()
        refresh_task = None

        async def text_refresh_loop(live_obj: Live, agent_name: str) -> None:
            last_text = ""
            while text_stream_active:
                try:
                    await asyncio.wait_for(text_updated.wait(), timeout=0.05)
                    text_updated.clear()
                except asyncio.TimeoutError:
                    pass

                current_text = "".join(text_buffer)
                if current_text != last_text:
                    live_obj.update(
                        Panel(
                            Markdown(current_text),
                            title=f"[agent]🤖 {agent_name}[/agent]",
                            border_style="cyan",
                            padding=(1, 2),
                        )
                    )
                    live_obj.refresh()
                    last_text = current_text

        async def reasoning_refresh_loop(live_obj: Live, agent_name: str) -> None:
            last_reasoning = ""
            while reasoning_stream_active:
                try:
                    await asyncio.wait_for(reasoning_updated.wait(), timeout=0.05)
                    reasoning_updated.clear()
                except asyncio.TimeoutError:
                    pass

                current_reasoning = "".join(reasoning_buffer)
                if current_reasoning != last_reasoning:
                    live_obj.update(
                        Panel(
                            current_reasoning,
                            title=f"[thinking]💭 {agent_name} thinking...[/thinking]",
                            border_style="dim",
                            padding=(1, 2),
                        )
                    )
                    live_obj.refresh()
                    last_reasoning = current_reasoning

        try:
            async for chunk in chunk_iter:
                if isinstance(chunk, TextDelta):
                    # Finish reasoning loop if it was active
                    if live and reasoning_stream_active:
                        reasoning_stream_active = False
                        reasoning_updated.set()
                        if refresh_task:
                            await refresh_task
                        final_reasoning = "".join(reasoning_buffer)
                        live.update(
                            Panel(
                                final_reasoning,
                                title=f"[thinking]💭 {self.agent.name} thinking...[/thinking]",
                                border_style="dim",
                                padding=(1, 2),
                            )
                        )
                        live.stop()
                        live = None
                        refresh_task = None

                    # Start text streaming if not already started
                    if not live:
                        live = Live(
                            Panel(
                                Markdown(""),
                                title=f"[agent]🤖 {self.agent.name}[/agent]",
                                border_style="cyan",
                                padding=(1, 2),
                            ),
                            console=self.console,
                            auto_refresh=False,
                        )
                        live.start()
                        text_stream_active = True
                        text_updated.set()
                        refresh_task = asyncio.create_task(
                            text_refresh_loop(live, self.agent.name)
                        )

                    text_buffer.append(chunk.text)
                    text_updated.set()

                elif isinstance(chunk, ReasoningDelta):
                    # Finish text loop if it was active (unlikely, but safe)
                    if live and text_stream_active:
                        text_stream_active = False
                        text_updated.set()
                        if refresh_task:
                            await refresh_task
                        final_text = "".join(text_buffer)
                        live.update(
                            Panel(
                                Markdown(final_text),
                                title=f"[agent]🤖 {self.agent.name}[/agent]",
                                border_style="cyan",
                                padding=(1, 2),
                            )
                        )
                        live.stop()
                        live = None
                        refresh_task = None

                    # Start reasoning streaming if not already started
                    if not live:
                        live = Live(
                            Panel(
                                "",
                                title=f"[thinking]💭 {self.agent.name} thinking...[/thinking]",
                                border_style="dim",
                                padding=(1, 2),
                            ),
                            console=self.console,
                            auto_refresh=False,
                        )
                        live.start()
                        reasoning_stream_active = True
                        reasoning_updated.set()
                        refresh_task = asyncio.create_task(
                            reasoning_refresh_loop(live, self.agent.name)
                        )

                    reasoning_buffer.append(chunk.text)
                    reasoning_updated.set()

                elif isinstance(chunk, CompletionEvent):
                    if hasattr(chunk, "content"):
                        from ravi.kernel.core.content import content_blocks_to_str

                        final_message = content_blocks_to_str(chunk.content)
                    else:
                        final_message = getattr(chunk, "message", None)
                    if live:
                        if text_stream_active:
                            text_stream_active = False
                            text_updated.set()
                            if refresh_task:
                                await refresh_task
                            final_text = "".join(text_buffer)
                            live.update(
                                Panel(
                                    Markdown(final_text),
                                    title=f"[agent]🤖 {self.agent.name}[/agent]",
                                    border_style="cyan",
                                    padding=(1, 2),
                                )
                            )
                        elif reasoning_stream_active:
                            reasoning_stream_active = False
                            reasoning_updated.set()
                            if refresh_task:
                                await refresh_task
                            final_reasoning = "".join(reasoning_buffer)
                            live.update(
                                Panel(
                                    final_reasoning,
                                    title=f"[thinking]💭 {self.agent.name} thinking...[/thinking]",
                                    border_style="dim",
                                    padding=(1, 2),
                                )
                            )
                        live.stop()
                        live = None
                        refresh_task = None

                elif isinstance(chunk, AgentProgress):
                    if chunk.step == AgentStep.TOOL_CALL:
                        if live:
                            if text_stream_active:
                                text_stream_active = False
                                text_updated.set()
                                if refresh_task:
                                    await refresh_task
                                final_text = "".join(text_buffer)
                                live.update(
                                    Panel(
                                        Markdown(final_text),
                                        title=f"[agent]🤖 {self.agent.name}[/agent]",
                                        border_style="cyan",
                                        padding=(1, 2),
                                    )
                                )
                            elif reasoning_stream_active:
                                reasoning_stream_active = False
                                reasoning_updated.set()
                                if refresh_task:
                                    await refresh_task
                                final_reasoning = "".join(reasoning_buffer)
                                live.update(
                                    Panel(
                                        final_reasoning,
                                        title=f"[thinking]💭 {self.agent.name} thinking...[/thinking]",
                                        border_style="dim",
                                        padding=(1, 2),
                                    )
                                )
                            live.stop()
                            live = None
                            refresh_task = None
                        tool_calls_count += 1
                        self._print_tool_call(chunk)
                    elif chunk.step == AgentStep.TOOL_RESULT:
                        self._print_tool_result(chunk)

        finally:
            # Set active flags to False to shut down background tasks cleanly
            text_stream_active = False
            reasoning_stream_active = False
            text_updated.set()
            reasoning_updated.set()
            if refresh_task:
                try:
                    await refresh_task
                except Exception:
                    pass
            if live:
                try:
                    # Final flush of active stream
                    if text_buffer:
                        final_text = "".join(text_buffer)
                        live.update(
                            Panel(
                                Markdown(final_text),
                                title=f"[agent]🤖 {self.agent.name}[/agent]",
                                border_style="cyan",
                                padding=(1, 2),
                            )
                        )
                    elif reasoning_buffer:
                        final_reasoning = "".join(reasoning_buffer)
                        live.update(
                            Panel(
                                final_reasoning,
                                title=f"[thinking]💭 {self.agent.name} thinking...[/thinking]",
                                border_style="dim",
                                padding=(1, 2),
                            )
                        )
                except Exception:
                    pass
                try:
                    live.stop()
                except Exception:
                    pass

        elapsed = time.monotonic() - t0
        self._print_stream_footer(elapsed, tool_calls_count)
        return final_message

    # ------------------------------------------------------------------
    # Static stream watcher (for attaching to any async iterator)
    # ------------------------------------------------------------------

    @staticmethod
    async def stream(
        iterator: AsyncIterator[Any],
        *,
        output: Optional[RichConsole] = None,
    ) -> AsyncIterator[Any]:
        """Wrap any agent ``run_stream()`` iterator with pretty printing.

        Usage::

            async for chunk in Console.stream(agent.run_stream("hi")):
                pass  # chunks are still yielded for downstream processing
        """

        con = output or RichConsole(theme=_THEME, highlight=False)
        partial_text = ""

        async for chunk in iterator:
            if isinstance(chunk, TextDelta):
                partial_text += chunk.text
                con.print(chunk.text, end="", style="")
                if hasattr(con.file, "flush"):
                    con.file.flush()
                await asyncio.sleep(0)
            elif isinstance(chunk, ReasoningDelta):
                con.print(chunk.text, end="", style="thinking")
                if hasattr(con.file, "flush"):
                    con.file.flush()
                await asyncio.sleep(0)
            elif isinstance(chunk, CompletionEvent):
                if partial_text:
                    con.print()  # newline
                    partial_text = ""
            elif isinstance(chunk, ToolExecutionResult):
                con.print()
                _print_tool_result_static(con, chunk)
            yield chunk

    # ------------------------------------------------------------------
    # Interactive REPL
    # ------------------------------------------------------------------

    async def interactive(
        self,
        *,
        greeting: Optional[str] = None,
        stream: bool = True,
    ) -> None:
        """Run an interactive chat loop.

        Type ``exit``, ``quit``, or press Ctrl-C to leave.
        Type ``/reset`` to clear agent memory.
        Type ``/tools`` to list available tools.
        Type ``/skills`` to list discovered skills and active ones.
        """
        name = getattr(self.agent, "name", "Agent")

        tools = self._get_tools()
        tool_count = len(tools)

        agent_skills = self._get_agent_skills()
        skill_count = (
            len(self._skill_manager.available_names)
            if self._skill_manager
            else len(agent_skills)
        )
        active_count = (
            len(self._skill_manager._active)
            if self._skill_manager
            else len(agent_skills)
        )

        skill_summary = (
            f"[bold]{skill_count} skills available[/bold]"
            if skill_count
            else "no skills"
        )
        if active_count:
            skill_summary += f" ([skill]{active_count} active[/skill])"

        if greeting is None:
            greeting = (
                f"[agent]{name}[/agent] ready · "
                f"[bold]{tool_count} tools[/bold] · {skill_summary}\n"
                f"  [dim]/tools · /skills · /reset · /help · exit[/dim]"
            )

        self.console.print(Panel(greeting, border_style="cyan", padding=(0, 1)))

        while True:
            try:
                user_input = self._prompt()
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n👋 Bye!", style="info")
                break

            stripped = user_input.strip()
            if not stripped:
                continue
            if stripped.lower() in ("exit", "quit", "/exit", "/quit"):
                self.console.print("👋 Bye!", style="info")
                break
            if stripped.lower() == "/reset":
                if hasattr(self.agent, "reset"):
                    await self.agent.reset()
                elif hasattr(self.agent, "_context") and hasattr(self.agent._context, "history"):
                    await self.agent._context.history.clear(self.agent.id, session_id=self._correlation_id)
                self.console.print("🔄 Agent memory cleared.", style="info")
                continue
            if stripped.lower() == "/tools":
                self._print_tools()
                continue
            if stripped.lower() == "/skills":
                self._print_skills()
                continue
            if stripped.lower() == "/help":
                self._print_help()
                continue

            # Snapshot active skills before run to detect newly activated ones
            before = (
                set(self._skill_manager._active.keys())
                if self._skill_manager
                else set()
            )

            try:
                if stream:
                    await self.run_stream(stripped, _echo=False)
                else:
                    await self.run(stripped, _echo=False)
            except Exception as exc:
                self.console.print(f"[error]Error: {exc}[/error]")

            # Report newly activated skills after this turn
            after = (
                set(self._skill_manager._active.keys())
                if self._skill_manager
                else set()
            )
            newly_activated = after - before
            if newly_activated:
                self._session_skills_used.update(newly_activated)
                names = ", ".join(sorted(newly_activated))
                self.console.print(
                    f"  [skill]⚡ Skill activated: {names}[/skill]", style="bold"
                )

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _prompt(self) -> str:
        """Read user input (works in both terminal and Jupyter)."""
        try:
            return self.console.input("\n[user]👤 You → [/user]")
        except UnsupportedOperation:
            # Fallback for Jupyter
            return input("\nYou → ")

    def _print_user(self, text: str) -> None:
        self.console.print(f"\n[user]👤 You →[/user] {text}")

    def _print_tool_call(self, chunk: AgentProgress) -> None:
        depth = getattr(chunk, "depth", 0) or 0
        indent = "  " * (depth + 1)
        prefix = f"[dim]\\[{chunk.agent_id.key}][/dim] " if depth > 0 else ""
        self.console.print(
            f"{indent}[dim]→ tool:[/dim] {prefix}[tool_name]{chunk.content}[/tool_name]"
        )

    def _print_tool_result(self, chunk: AgentProgress) -> None:
        depth = getattr(chunk, "depth", 0) or 0
        indent = "  " * (depth + 1)
        is_err = "error" in chunk.content
        icon = "✖" if is_err else "✔"
        style = "tool_err" if is_err else "tool_ok"
        prefix = f"[dim]\\[{chunk.agent_id.key}][/dim] " if depth > 0 else ""
        self.console.print(f"{indent}{icon} {prefix}[{style}]{chunk.content}[/{style}]")

    def _print_result(self, result: Any, elapsed: float) -> None:
        """Pretty-print an AgentRunResult."""
        # Output text (supporting both new 'output' and old 'output_text' fields)
        output_text = getattr(result, "output", "")
        if not output_text:
            output_text = getattr(result, "output_text", "")
        if not output_text and isinstance(result, str):
            output_text = result
        if not output_text and hasattr(result, "__dict__"):
            output_text = str(result)

        if output_text:
            self.console.print()
            self.console.print(
                Panel(
                    Markdown(output_text),
                    title=f"[agent]🤖 {getattr(self.agent, 'name', 'Agent')}[/agent]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )

        # Footer
        steps = getattr(result, "steps_used", "?")
        if steps == "?":
            if hasattr(result, "tool_calls"):
                steps = len(result.tool_calls) + 1
            else:
                steps = 1

        tokens = getattr(result, "usage", None)
        token_str = ""
        if tokens:
            if hasattr(tokens, "total_tokens"):
                token_str = f"{tokens.total_tokens} tokens"
            elif hasattr(tokens, "total") and isinstance(tokens.total, int):
                token_str = f"{tokens.total} tokens"

        tools = 0
        if hasattr(result, "tool_calls"):
            tools = len(result.tool_calls)
        elif hasattr(result, "tool_calls_total"):
            tools = result.tool_calls_total

        status_str = "completed"
        if hasattr(result, "status"):
            status_val = result.status
            if hasattr(status_val, "value"):
                status_str = str(status_val.value)
            else:
                status_str = str(status_val)

        parts = [
            f"{status_str}",
            f"{steps} steps",
            f"{tools} tool calls",
            token_str,
            f"{elapsed:.1f}s",
        ]
        footer = " · ".join(p for p in parts if p)
        self.console.print(f"  [info]{footer}[/info]")

    def _print_stream_footer(
        self, elapsed: float, tool_calls: int, steps: int = 1, status: str = "completed"
    ) -> None:
        parts = [
            f"{status}",
            f"{steps} steps",
            f"{tool_calls} tool calls" if tool_calls else "0 tool calls",
            f"{elapsed:.1f}s",
        ]
        self.console.print(f"\n  [info]{' · '.join(parts)}[/info]")

    # ------------------------------------------------------------------
    # Internal: tool / skill accessors
    # ------------------------------------------------------------------

    def _get_tools(self) -> list[Any]:
        """Return tool list from agent (supports multiple agent shapes)."""
        tools = getattr(self.agent, "tools", [])
        if not tools and hasattr(self.agent, "list_tools"):
            tools = self.agent.list_tools()
        if not tools and hasattr(self.agent, "_tools"):
            _t = getattr(self.agent, "_tools")
            tools = list(_t.values()) if isinstance(_t, dict) else list(_t)
        return tools or []

    def _get_agent_skills(self) -> list[Any]:
        """Return skills that are already injected into the agent."""
        return list(getattr(self.agent, "_skills", []))

    def _print_tools(self) -> None:
        tools = self._get_tools()
        if not tools:
            self.console.print("  No tools registered.", style="info")
            return
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
        self.console.print(table)

    def _print_skills(self) -> None:
        """Print all discovered skills (from SkillManager) and agent-injected skills."""
        # Discovered skills from SkillManager
        if self._skill_manager:
            all_meta = self._skill_manager._loader.all_metadata()
            active_names = set(self._skill_manager._active.keys())

            if not all_meta:
                self.console.print("  No skills discovered.", style="info")
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
                self.console.print(table)

            if self._session_skills_used:
                names = ", ".join(sorted(self._session_skills_used))
                self.console.print(f"  [skill]Session activated: {names}[/skill]")
        else:
            # Fall back to agent-injected skills
            agent_skills = self._get_agent_skills()
            if not agent_skills:
                self.console.print("  No skills loaded.", style="info")
                return
            table = Table(title="Active Skills", show_lines=False, padding=(0, 1))
            table.add_column("Name", style="skill")
            table.add_column("Tools", style="dim")
            for s in agent_skills:
                name = getattr(s, "name", "?")
                allowed = ", ".join(getattr(s, "allowed_tools", [])) or "—"
                table.add_row(name, allowed)
            self.console.print(table)

    def _print_help(self) -> None:
        help_text = (
            "[bold]Commands:[/bold]\n"
            "  /tools  — List available tools\n"
            "  /skills — List discovered skills and active ones\n"
            "  /reset  — Clear agent memory\n"
            "  /help   — Show this message\n"
            "  exit    — Quit the session"
        )
        self.console.print(Panel(help_text, border_style="dim", padding=(0, 1)))


# ---------------------------------------------------------------------------
# Module-level helper (shared by instance and static methods)
# ---------------------------------------------------------------------------


def _print_tool_result_static(con: RichConsole, msg: Any) -> None:
    """Render a single tool execution result."""
    name = getattr(msg, "name", "tool")
    is_err = getattr(msg, "is_error", False)
    content = getattr(msg, "content", [])

    # Extract text from MCP content blocks
    text_parts: List[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif isinstance(block, str):
            text_parts.append(block)

    result_text = "\n".join(text_parts)

    # Try to pretty-format JSON results
    try:
        parsed = json.loads(result_text)
        result_text = json.dumps(parsed, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass

    # Truncate very long results
    if len(result_text) > 500:
        result_text = result_text[:497] + "..."

    style = "tool_err" if is_err else "tool_ok"
    icon = "✖" if is_err else "✔"
    con.print(f"  {icon} [tool_name]{name}[/tool_name]  ", end="")
    con.print(result_text, style=style)
