"""AgentContext and DefaultAgentContext concrete impls."""

from __future__ import annotations

from ravi.kernel import AgentId, Message
from ravi.kernel.context import AgentContextProtocol, CompactionStrategy
from ravi.kernel.history import HistoryProvider
from .compaction import SlidingWindowCompaction


class AgentContext:
    """User-facing context config — pass to ``AssistantAgent(context=...)``.

    Usage::

        # Explicit
        context = AgentContext(
            InMemoryHistoryProvider(),
            [SlidingWindowCompaction(max_messages=40)],
        )
        agent = AssistantAgent("bot", runtime, model=client, context=context)

        # Default (in-memory, sliding-window 100)
        context = AgentContext.default()

    When ``compaction_strategies`` is a list the first strategy is used.
    """

    def __init__(
        self,
        history: HistoryProvider,
        compaction_strategies: list[CompactionStrategy]
        | CompactionStrategy
        | None = None,
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

    @classmethod
    def default(cls) -> AgentContext:
        """Return an in-memory context with default sliding-window compaction."""
        from ravi.agents.context.history import InMemoryHistoryProvider

        return cls(InMemoryHistoryProvider())


class DefaultAgentContext:
    """Concrete AgentContext for in-process use."""

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


__all__ = ["AgentContextProtocol", "AgentContext", "DefaultAgentContext"]
