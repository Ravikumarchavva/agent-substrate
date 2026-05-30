from __future__ import annotations

from typing import Protocol

from ravi.kernel import AgentId, Message
from .history import HistoryProvider
from .compaction import CompactionStrategy


class AgentContext(Protocol):
    """Runtime context bound to a specific agent's execution loop.

    Bridges the agent's identity, its raw history, and the compacted
    window it feeds to the LLM.
    """

    @property
    def agent_id(self) -> AgentId: ...

    @property
    def history(self) -> HistoryProvider: ...

    @property
    def compaction(self) -> CompactionStrategy: ...

    async def get_prompt_window(self) -> list[Message]:
        """Fetch history and apply the compaction strategy.

        Returns the message sequence ready for the LLM encoder.
        """
        ...
