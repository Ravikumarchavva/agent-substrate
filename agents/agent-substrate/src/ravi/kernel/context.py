"""Agent context and compaction contracts."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.content import ChatMessage
from ravi.kernel.identity import AgentId


class CompactionStrategy(Protocol):
    """Converts raw message history into a manageable LLM context window.

    Implementations might use sliding windows, token truncation, or
    LLM-based summarisation.  Input and output are ``list[ChatMessage]``
    — the same type used directly by ``LLMClient.generate``.
    """

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        """Return the optimised sequence ready for LLM generation."""
        ...


class AgentContextProtocol(Protocol):
    """Structural protocol for the agent's runtime context.

    Exposes only what the agent loop needs: the agent's own id and a
    way to retrieve the compacted prompt window for a given session.
    Internal storage details (HistoryProvider, CompactionStrategy) are
    implementation concerns, not part of the public protocol.
    """

    @property
    def agent_id(self) -> AgentId: ...

    async def get_prompt_window(self, session_id: str) -> list[ChatMessage]: ...
