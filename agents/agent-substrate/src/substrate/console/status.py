"""Status line — a persistent dim summary of the active/finished turn.

Shows model · elapsed · tool count · token usage, refreshed live during a turn
and printed as a footer when it ends.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text

from .theme import ConsoleTheme


def model_name(agent: Any) -> str:
    """Best-effort model identifier for the agent's LLM client."""
    for attr in ("model", "llm", "_model", "client", "_client"):
        obj = getattr(agent, attr, None)
        if obj is None:
            continue
        name = getattr(obj, "model", None)
        if isinstance(name, str) and name:
            return name
        if isinstance(obj, str) and obj:
            return obj
    return "agent"


@dataclass
class StatusLine:
    """Mutable per-turn status; rendered into a single dim line."""

    model: str = "agent"
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def render(
        self, theme: ConsoleTheme, *, done: bool = False, failed: bool = False
    ) -> Text:
        verb = "failed" if failed else ("completed" if done else "running")
        tools = f"{self.tool_calls} tool{'s' if self.tool_calls != 1 else ''}"
        parts = [verb, tools, f"{self.elapsed:.1f}s"]
        total = self.input_tokens + self.output_tokens
        if total:
            parts.append(f"{_humanize(total)} tok")
        parts.append(self.model)
        style = "error" if failed else "info"
        return Text("  " + " · ".join(parts), style=style)


def _humanize(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
