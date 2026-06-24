"""Inline live renderer — commit-on-complete with a tiny transient Live region.

Finished blocks (reasoning, each tool row, the assistant reply, the subagent
tree) are *committed* to scrollback exactly once. The ``Live`` region only ever
holds ephemeral state — the block currently streaming, spinners for in-flight
tools, and a transient status footer — so it never grows past the screen and is
robust against third-party libraries that print to stdout mid-turn.

A sequential (no-``Live``) path handles notebooks / non-TTY / wide-character text.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Optional

from rich.console import Console as RichConsole, Group, RenderableType
from rich.live import Live

from substrate.kernel.core.content import content_blocks_to_str
from substrate.kernel.messaging.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)

from .hitl import ConsoleHumanHandler, _HITLRequest, render_hitl_panel
from .status import StatusLine
from .stream_adapter import _RunFailed
from .subagents import SubagentTracker
from .taskboard import _TaskBoardUpdate, render_task_board
from .theme import ConsoleTheme
from .tools import ToolCall, render_tool_rows
from .widgets import (
    assistant_panel,
    error_panel,
    has_wide_or_combining_characters,
    reasoning_panel,
)

_FAIL_TITLES = {
    "guardrail_tripped": "Request blocked",
    "budget_exhausted": "Budget exhausted",
    "cancelled": "Cancelled",
    "agent_crashed": "Run failed",
}

_REFRESH_INTERVAL = 0.08  # seconds — throttle live updates


class LiveTurn:
    """Render a single agent turn from a stream of UI events.

    Streaming blocks are shown in a transient ``Live`` region and committed to
    scrollback the moment they finish; nothing is rendered twice.
    """

    def __init__(
        self,
        console: RichConsole,
        *,
        name: str,
        theme: ConsoleTheme,
        status: StatusLine,
        hitl_handler: ConsoleHumanHandler | None = None,
        signal_bus: Optional[Any] = None,
    ) -> None:
        self.console = console
        self.name = name
        self.theme = theme
        self.status = status
        self._hitl_handler = hitl_handler
        self._signal_bus = signal_bus

        # Ephemeral state held in the live region until committed.
        self._section: str | None = None  # "reasoning" | "text" | None
        self._reasoning = ""
        self._assistant = ""
        self._active_tools: list[ToolCall] = []
        self._subagents = SubagentTracker()
        self._final = ""
        self.failed = False
        # Set by _handle_hitl when the user types free text instead of picking.
        # interactive() checks this and re-submits as a new turn.
        self.pending_followup: str | None = None

        self._live: Live | None = None
        self._sequential = not console.is_terminal
        self._seq_section: str | None = None
        self._last_refresh = 0.0

    async def consume(self, events: AsyncIterator[Any]) -> str:
        """Drive the turn to completion; returns the final assistant message."""
        try:
            async for ev in events:
                if isinstance(ev, _HITLRequest):
                    await self._handle_hitl(ev)
                else:
                    self._handle(ev)
        finally:
            self._finalize()
        return self._final or self._assistant

    async def _handle_hitl(self, ev: _HITLRequest) -> None:
        """Pause rendering, show the option card, collect input, resume.

        Input rules at the ``→`` prompt:
          - digit in option range  → answered with selected option
          - N+1 (freeform slot)    → prompts for text → answered with freeform
          - s / empty              → skipped (agent uses best judgement)
          - any other text         → cancelled; text stashed in pending_followup
                                     for interactive() to re-submit as new turn
        """
        self._stop_live()
        self.console.print(render_hitl_panel(ev, self.theme))

        n_opts = len(ev.options)
        loop = asyncio.get_running_loop()

        signal_payload: dict[str, Any] | None = None
        while signal_payload is None:
            raw: str = await loop.run_in_executor(
                None, lambda: input("\n  → ").strip()
            )
            lower = raw.lower()

            if lower in ("s", "skip", ""):
                signal_payload = {"action": "skipped"}
                self.console.print("  [dim]Skipped[/dim]\n")

            elif raw.isdigit():
                n = int(raw)
                if 1 <= n <= n_opts:
                    opt = ev.options[n - 1]
                    signal_payload = {
                        "action": "answered",
                        "selected_key": opt.key,
                        "selected_label": opt.label,
                    }
                    self.console.print(f"  [dim]Selected: {opt.label}[/dim]\n")
                elif ev.allow_freeform and n == n_opts + 1:
                    text: str = await loop.run_in_executor(
                        None, lambda: input("  Your answer: ").strip()
                    )
                    if text:
                        signal_payload = {
                            "action": "answered",
                            "freeform_text": text,
                        }
                        self.console.print(f"  [dim]Input: {text}[/dim]\n")

            else:
                # Free text that isn't a numbered choice: cancel the pending
                # HITL call and stash the text so interactive() can resubmit.
                signal_payload = {"action": "cancelled"}
                self.pending_followup = raw
                self.console.print("  [dim]Cancelled — will resubmit your message[/dim]\n")

        # Fire the signal to resume the suspended run.
        if self._signal_bus is not None and ev.run_id:
            await self._signal_bus.signal(
                ev.run_id, f"hitl:{ev.request_id}", signal_payload
            )

    # ── event handling ────────────────────────────────────────────────────
    def _handle(self, ev: Any) -> None:
        if isinstance(ev, (TextDelta, ReasoningDelta)) and not self._sequential:
            if has_wide_or_combining_characters(ev.text):
                self._switch_to_sequential()

        if isinstance(ev, TextDelta):
            if self._section == "reasoning":
                self._commit_reasoning()
            self._section = "text"
            self._assistant += ev.text
            self._on_stream("text", ev.text)
        elif isinstance(ev, ReasoningDelta):
            self._section = "reasoning"
            self._reasoning += ev.text
            self._on_stream("reasoning", ev.text)
        elif isinstance(ev, AgentProgress):
            self._on_progress(ev)
        elif isinstance(ev, CompletionEvent):
            self._final = content_blocks_to_str(ev.content)  # type: ignore[arg-type]
            self._commit_assistant()
        elif isinstance(ev, _TaskBoardUpdate):
            self._on_taskboards(ev.boards)
        elif isinstance(ev, _RunFailed):
            self._on_failed(ev)
        elif isinstance(ev, StreamDone):
            pass

    def _on_failed(self, ev: _RunFailed) -> None:
        self.failed = True
        self._commit_streaming()
        self._stop_live()
        title = _FAIL_TITLES.get(ev.status, "Run failed")
        self.console.print(error_panel(ev.message, self.theme, title=title))

    def _on_stream(self, section: str, text: str) -> None:
        if self._sequential:
            self._seq_print(section, text)
        else:
            self._refresh()

    def _on_progress(self, ev: AgentProgress) -> None:
        # Subagent events (depth>0) feed the tree; depth-0 are main-agent tools.
        if ev.depth > 0:
            self._subagents.ingest(ev)
            if self._sequential:
                self._seq_subagent(ev)
            else:
                self._refresh()
            return

        if ev.step == AgentStep.TOOL_CALL:
            self._commit_streaming()  # a tool call ends the current text block
            self.status.tool_calls += 1
            call = ToolCall(name=ev.content, agent_key=ev.agent_id.key, depth=ev.depth)
            self._active_tools.append(call)
            if self._sequential:
                self._seq_tool_running(call)
            else:
                self._refresh()
        elif ev.step == AgentStep.TOOL_RESULT:
            is_err = "error" in ev.content
            base = (
                ev.content[:-6]
                if is_err and ev.content.endswith(" error")
                else ev.content
            )
            call = self._take_active_tool(base)
            if call is not None:
                call.finish(is_error=is_err)
                self._commit_tool(call)
            if self._sequential:
                self._seq_tool_done(call, base, is_err)
            else:
                self._refresh()

    def _take_active_tool(self, name: str) -> ToolCall | None:
        for i in range(len(self._active_tools) - 1, -1, -1):
            if self._active_tools[i].name == name:
                return self._active_tools.pop(i)
        return None

    def _on_taskboards(self, boards: list[Any]) -> None:
        self._commit_streaming()
        self._stop_live()
        for board in boards:
            panel = render_task_board(board, self.theme)
            if panel is not None:
                self.console.print(panel)

    # ── commit (move finished blocks into permanent scrollback) ────────────
    def _commit_streaming(self) -> None:
        if self._section == "reasoning":
            self._commit_reasoning()
        elif self._section == "text":
            self._commit_assistant()

    def _commit_reasoning(self) -> None:
        text, self._reasoning, self._section = self._reasoning, "", None
        if not text:
            return
        if self._sequential:
            self._seq_close()
            return
        self._stop_live()
        self.console.print(reasoning_panel(text, self.name, self.theme))

    def _commit_assistant(self) -> None:
        if self._section == "text":
            self._section = None
        text = self._assistant
        if not text:
            return
        if self._sequential:
            self._seq_close()
            return
        self._stop_live()
        self.console.print(assistant_panel(text, self.name, self.theme))
        self._assistant = ""  # committed; don't re-render in the live region

    def _commit_tool(self, call: ToolCall) -> None:
        if self._sequential:
            return
        self._stop_live()
        self.console.print(render_tool_rows([call], self.theme))

    # ── live region (ephemeral only) ──────────────────────────────────────
    def _render_live(self) -> RenderableType:
        parts: list[RenderableType] = []
        tree = self._subagents.render(self.theme)
        if tree is not None:
            parts.append(tree)
        if self._section == "reasoning" and self._reasoning:
            parts.append(reasoning_panel(self._reasoning, self.name, self.theme))
        if self._active_tools:
            parts.append(render_tool_rows(self._active_tools, self.theme))
        if self._section == "text" and self._assistant:
            parts.append(assistant_panel(self._assistant, self.name, self.theme))
        parts.append(self.status.render(self.theme, done=False))
        return Group(*parts)

    def _refresh(self, *, force: bool = False) -> None:
        if self._sequential:
            return
        now = time.monotonic()
        if not force and now - self._last_refresh < _REFRESH_INTERVAL:
            return
        self._last_refresh = now
        renderable = self._render_live()
        if self._live is None:
            self._live = Live(
                renderable, console=self.console, auto_refresh=False, transient=True
            )
            self._live.start()
        self._live.update(renderable)
        self._live.refresh()

    def _stop_live(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()  # transient → erases the ephemeral region
            except Exception:
                pass
            self._live = None

    def _finalize(self) -> None:
        # Commit whatever is still streaming, then persist the final subagent
        # tree and clear the transient footer.
        self._commit_streaming()
        if not self._sequential:
            self._stop_live()
            tree = self._subagents.render(self.theme)
            if tree is not None:
                self.console.print(tree)
        else:
            self._seq_close()

    # ── sequential (no-Live) fallback ─────────────────────────────────────
    def _switch_to_sequential(self) -> None:
        self._stop_live()
        self._sequential = True

    def _seq_close(self) -> None:
        if self._seq_section is not None:
            self.console.print()
            self._seq_section = None

    def _seq_subagent(self, ev: AgentProgress) -> None:
        self._seq_close()
        icon = self.theme.handoff_icon
        if ev.step == AgentStep.DONE:
            detail = "done"
        elif ev.step == AgentStep.ERROR:
            detail = "error"
        else:
            detail = "running…"
        self.console.print(
            f"  [subagent]{icon} {ev.agent_id.key}[/subagent] [info]{detail}[/info]"
        )

    def _seq_print(self, section: str, text: str) -> None:
        if self._seq_section != section:
            self._seq_close()
            if section == "text":
                self.console.print(
                    f"\n[agent]{self.theme.assistant_icon} {self.name}:[/agent]\n",
                    end="",
                )
            else:
                self.console.print(
                    f"\n[thinking]{self.theme.thinking_icon} {self.name} thinking…[/thinking]\n",
                    end="",
                )
            self._seq_section = section
        style = "thinking" if section == "reasoning" else ""
        self.console.print(text, end="", style=style)
        if hasattr(self.console.file, "flush"):
            self.console.file.flush()

    def _seq_tool_running(self, call: ToolCall) -> None:
        self._seq_close()
        indent = "  " * (call.depth + 1)
        self.console.print(
            f"{indent}[dim]→ tool:[/dim] [tool_name]{call.name}[/tool_name]"
        )

    def _seq_tool_done(self, call: ToolCall | None, name: str, is_err: bool) -> None:
        icon = self.theme.err_icon if is_err else self.theme.ok_icon
        style = "tool_err" if is_err else "tool_ok"
        dur = f" [info]{call.duration:.1f}s[/info]" if call and call.duration else ""
        self.console.print(f"    {icon} [{style}]{name}[/{style}]{dur}")
