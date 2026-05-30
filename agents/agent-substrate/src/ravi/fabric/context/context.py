from __future__ import annotations

from typing import Protocol

from ravi.kernel import AgentId, Message
from .history import HistoryProvider
from .compaction import CompactionStrategy, SlidingWindowCompaction


class AgentContextProtocol(Protocol):
    """Structural protocol for the agent's runtime context."""

    @property
    def agent_id(self) -> AgentId: ...

    @property
    def history(self) -> HistoryProvider: ...

    @property
    def compaction(self) -> CompactionStrategy: ...

    async def get_prompt_window(self) -> list[Message]: ...


class AgentContext:
    """User-facing context config — pass to ``AssistantAgent(context=...)``.

    Usage::

        context = AgentContext(
            InMemoryHistoryProvider(),
            [SlidingWindowCompaction(max_messages=40)],
        )
        agent = AssistantAgent("bot", runtime, model=client, context=context)

    When ``compaction_strategies`` is a list the first strategy is used.
    """

    def __init__(
        self,
        history: HistoryProvider,
        compaction_strategies: list[CompactionStrategy] | CompactionStrategy | None = None,
    ) -> None:
        self.history = history
        if isinstance(compaction_strategies, list):
            self.compaction: CompactionStrategy = (
                compaction_strategies[0]
                if compaction_strategies
                else SlidingWindowCompaction()
            )
        elif compaction_strategies is not None:
            self.compaction = compaction_strategies
        else:
            self.compaction = SlidingWindowCompaction()


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
