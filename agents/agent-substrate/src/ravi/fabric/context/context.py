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


class DefaultAgentContext:
    """Concrete AgentContext for in-process use.

    Wire up by passing an AgentId, a HistoryProvider, and a CompactionStrategy.
    The ``get_prompt_window`` method fetches raw history and applies compaction.
    """

    def __init__(
        self,
        agent_id: AgentId,
        history: HistoryProvider,
        compaction: CompactionStrategy,
    ) -> None:
        self._agent_id = agent_id
        self._history = history
        self._compaction = compaction

    @property
    def agent_id(self) -> AgentId:
        return self._agent_id

    @property
    def history(self) -> HistoryProvider:
        return self._history

    @property
    def compaction(self) -> CompactionStrategy:
        return self._compaction

    async def get_prompt_window(self) -> list[Message]:
        raw = await self._history.get_messages(self._agent_id)
        return await self._compaction.compact(raw)
