"""Agent context and compaction contracts."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.identity import AgentId
from ravi.kernel.message import Message
from ravi.kernel.history import HistoryProvider


class CompactionStrategy(Protocol):
    """Converts raw message history into a manageable LLM context window.

    Implementations might use sliding windows, token truncation, or
    background summarisation.
    """

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        """Return the optimised sequence ready for LLM encoding."""
        ...


class AgentContextProtocol(Protocol):
    """Structural protocol for the agent's runtime context."""

    @property
    def agent_id(self) -> AgentId: ...

    @property
    def history(self) -> HistoryProvider: ...

    @property
    def compaction(self) -> CompactionStrategy: ...

    async def get_prompt_window(self, session_id: str) -> list[Message]: ...
