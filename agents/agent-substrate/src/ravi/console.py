"""Interactive console for running agents in CLI and notebooks.

Inspired by AutoGen's ``Console`` — provides a rich, formatted view of agent
execution including streaming text, tool calls, reasoning traces, and results.

Supports both actor-model agents (``ActorAgent`` subclasses) and legacy callable
agents.  For actor-model agents, a ``UserProxyAgent`` is created transparently.

Usage (single task)::

    from ravi.console import Console

    result = await Console(agent).run("What is 2+2?")

Usage (interactive REPL)::

    await Console(agent).interactive()

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
from typing import Any, AsyncIterator, Optional, List

from rich.console import Console as RichConsole, Group, ConsoleOptions, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme
from rich.segment import Segment

from ravi.logger import setup_logging


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
        "info": "dim",
        "error": "bold red",
    }
)

# Lazy imports — avoid circular deps at module level
_TextDeltaChunk = None
_ReasoningDeltaChunk = None
_CompletionChunk = None
_ToolExecutionResultMessage = None


def _ensure_types() -> None:
    global \
        _TextDeltaChunk, \
        _ReasoningDeltaChunk, \
        _CompletionChunk, \
        _ToolExecutionResultMessage
    if _TextDeltaChunk is None:
        from ravi.kernel.messages._types import (
            TextDeltaChunk,
            ReasoningDeltaChunk,
            CompletionChunk,
        )
        from ravi.kernel.messages.client_messages import (
            ToolExecutionResultMessage,
        )

        _TextDeltaChunk = TextDeltaChunk
        _ReasoningDeltaChunk = ReasoningDeltaChunk
        _CompletionChunk = CompletionChunk
        _ToolExecutionResultMessage = ToolExecutionResultMessage


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
    ) -> None:
        self.agent = agent
        self.console = output or RichConsole(theme=_THEME, highlight=False)
        self._proxy: Optional[Any] = None

        # Configure logging for interactive use (once per process)
        setup_logging(mode="pretty", level=logging.WARNING)

    def _is_actor_agent(self) -> bool:
        """Return True if the agent uses the actor model (has on_message)."""
        from ravi.fabric.actors.actor import ActorAgent

        return isinstance(self.agent, ActorAgent)

    async def _get_proxy(self) -> Any:
        """Lazily create and start the UserProxyAgent for actor-model agents."""
        if self._proxy is None:
            from ravi.orchestration.agents.proxy.agent import UserProxyAgent

            self._proxy = UserProxyAgent(
                "console-proxy",
                self.agent.runtime,
                key=f"console-{id(self):x}",
            )
            await self._proxy.start()
        return self._proxy

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

        if self._is_actor_agent():
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
        _ensure_types()
        text_delta_cls = _TextDeltaChunk
        reasoning_delta_cls = _ReasoningDeltaChunk
        completion_cls = _CompletionChunk
        tool_result_cls = _ToolExecutionResultMessage
        assert text_delta_cls is not None
        assert reasoning_delta_cls is not None
        assert completion_cls is not None
        assert tool_result_cls is not None

        if _echo:
            self._print_user(task)
        t0 = time.monotonic()

        text_buffer: List[str] = []
        reasoning_buffer: List[str] = []
        final_message: Any = None
        tool_calls_count = 0

        if self._is_actor_agent():
            proxy = await self._get_proxy()
            channel = _ConsoleStreamChannel()
            # ask_stream() returns as soon as on_message() spawns the background task.
            await proxy.ask_stream(task, recipient=self.agent.id, channel=channel)
            chunk_iter: AsyncIterator[Any] = channel.__aiter__()
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
                        _build_accent_layout(
                            agent_name, Markdown(current_text), status="generating..."
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
                        _build_thinking_layout(
                            agent_name, current_reasoning, status="thinking..."
                        )
                    )
                    live_obj.refresh()
                    last_reasoning = current_reasoning

        try:
            async for chunk in chunk_iter:
                if isinstance(chunk, text_delta_cls):
                    # Finish reasoning loop if it was active
                    if live and reasoning_stream_active:
                        reasoning_stream_active = False
                        reasoning_updated.set()
                        if refresh_task:
                            await refresh_task
                        final_reasoning = "".join(reasoning_buffer)
                        live.update(
                            _build_thinking_layout(self.agent.name, final_reasoning)
                        )
                        live.stop()
                        live = None
                        refresh_task = None

                    # Start text streaming if not already started
                    if not live:
                        live = Live(
                            _build_accent_layout(
                                self.agent.name, Markdown(""), status="generating..."
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

                elif isinstance(chunk, reasoning_delta_cls):
                    # Finish text loop if it was active (unlikely, but safe)
                    if live and text_stream_active:
                        text_stream_active = False
                        text_updated.set()
                        if refresh_task:
                            await refresh_task
                        final_text = "".join(text_buffer)
                        live.update(
                            _build_accent_layout(self.agent.name, Markdown(final_text))
                        )
                        live.stop()
                        live = None
                        refresh_task = None

                    # Start reasoning streaming if not already started
                    if not live:
                        live = Live(
                            _build_thinking_layout(
                                self.agent.name, "", status="thinking..."
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

                elif isinstance(chunk, completion_cls):
                    final_message = chunk.message
                    if live:
                        if text_stream_active:
                            text_stream_active = False
                            text_updated.set()
                            if refresh_task:
                                await refresh_task
                            final_text = "".join(text_buffer)
                            live.update(
                                _build_accent_layout(
                                    self.agent.name, Markdown(final_text)
                                )
                            )
                        elif reasoning_stream_active:
                            reasoning_stream_active = False
                            reasoning_updated.set()
                            if refresh_task:
                                await refresh_task
                            final_reasoning = "".join(reasoning_buffer)
                            live.update(
                                _build_thinking_layout(self.agent.name, final_reasoning)
                            )
                        live.stop()
                        live = None
                        refresh_task = None

                elif isinstance(chunk, tool_result_cls):
                    if live:
                        if text_stream_active:
                            text_stream_active = False
                            text_updated.set()
                            if refresh_task:
                                await refresh_task
                            final_text = "".join(text_buffer)
                            live.update(
                                _build_accent_layout(
                                    self.agent.name, Markdown(final_text)
                                )
                            )
                        elif reasoning_stream_active:
                            reasoning_stream_active = False
                            reasoning_updated.set()
                            if refresh_task:
                                await refresh_task
                            final_reasoning = "".join(reasoning_buffer)
                            live.update(
                                _build_thinking_layout(self.agent.name, final_reasoning)
                            )
                        live.stop()
                        live = None
                        refresh_task = None
                    tool_calls_count += 1
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
                            _build_accent_layout(self.agent.name, Markdown(final_text))
                        )
                    elif reasoning_buffer:
                        final_reasoning = "".join(reasoning_buffer)
                        live.update(
                            _build_thinking_layout(self.agent.name, final_reasoning)
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
        _ensure_types()
        text_delta_cls = _TextDeltaChunk
        reasoning_delta_cls = _ReasoningDeltaChunk
        completion_cls = _CompletionChunk
        tool_result_cls = _ToolExecutionResultMessage
        assert text_delta_cls is not None
        assert reasoning_delta_cls is not None
        assert completion_cls is not None
        assert tool_result_cls is not None

        con = output or RichConsole(theme=_THEME, highlight=False)
        partial_text = ""

        async for chunk in iterator:
            if isinstance(chunk, text_delta_cls):
                partial_text += chunk.text
                con.print(chunk.text, end="", style="")
                if hasattr(con.file, "flush"):
                    con.file.flush()
                await asyncio.sleep(0)
            elif isinstance(chunk, reasoning_delta_cls):
                con.print(chunk.text, end="", style="thinking")
                if hasattr(con.file, "flush"):
                    con.file.flush()
                await asyncio.sleep(0)
            elif isinstance(chunk, completion_cls):
                if partial_text:
                    con.print()  # newline
                    partial_text = ""
            elif isinstance(chunk, tool_result_cls):
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
        """
        name = getattr(self.agent, "name", "Agent")
        tool_count = len(getattr(self.agent, "tools", []))

        if greeting is None:
            greeting = (
                f"[agent]{name}[/agent] ready "
                f"({tool_count} tools). Type [bold]/help[/bold] for commands."
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
                await self.agent.reset()
                self.console.print("🔄 Agent memory cleared.", style="info")
                continue
            if stripped.lower() == "/tools":
                self._print_tools()
                continue
            if stripped.lower() == "/help":
                self._print_help()
                continue

            try:
                if stream:
                    await self.run_stream(stripped, _echo=False)
                else:
                    await self.run(stripped, _echo=False)
            except Exception as exc:
                self.console.print(f"[error]Error: {exc}[/error]")

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

    def _print_tool_result(self, msg: Any) -> None:
        _print_tool_result_static(self.console, msg)

    def _print_result(self, result: Any, elapsed: float) -> None:
        """Pretty-print an AgentRunResult."""
        # Output text
        output_text = getattr(result, "output_text", str(result))
        if output_text:
            self.console.print()
            self.console.print(
                _build_accent_layout(result.agent_name, Markdown(output_text))
            )

        # Footer
        steps = getattr(result, "steps_used", "?")
        tokens = getattr(result, "usage", None)
        token_str = f"{tokens.total_tokens} tokens" if tokens else ""
        tools = getattr(result, "tool_calls_total", 0)
        status = getattr(result, "status", None)
        status_str = status.value if status else ""

        parts = [
            f"{status_str}",
            f"{steps} steps",
            f"{tools} tool calls",
            token_str,
            f"{elapsed:.1f}s",
        ]
        footer = " · ".join(p for p in parts if p)
        self.console.print(f"  [info]{footer}[/info]")

    def _print_stream_footer(self, elapsed: float, tool_calls: int) -> None:
        parts = []
        if tool_calls:
            parts.append(f"{tool_calls} tool calls")
        parts.append(f"{elapsed:.1f}s")
        self.console.print(f"\n  [info]{' · '.join(parts)}[/info]")

    def _print_tools(self) -> None:
        tools = getattr(self.agent, "tools", [])
        if not tools:
            self.console.print("  No tools registered.", style="info")
            return
        table = Table(title="Available Tools", show_lines=False, padding=(0, 1))
        table.add_column("Name", style="tool_name")
        table.add_column("Description", style="")
        for t in tools:
            name = getattr(t, "name", "?")
            desc = getattr(t, "description", "")
            # Truncate long descriptions
            if len(desc) > 80:
                desc = desc[:77] + "..."
            table.add_row(name, desc)
        self.console.print(table)

    def _print_help(self) -> None:
        help_text = (
            "[bold]Commands:[/bold]\n"
            "  /tools  — List available tools\n"
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
