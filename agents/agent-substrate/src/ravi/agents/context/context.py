"""AgentContext (protocol impl) and ContextConfig (user-facing config bag)."""

from __future__ import annotations

from ravi.kernel import AgentId, Message
from ravi.kernel.context import AgentContextProtocol, CompactionStrategy
from ravi.kernel.history import HistoryProvider
from .compaction import SlidingWindowCompaction


class ContextConfig:
    """User-facing config bag — pass to ``ReActAgent(context=...)``.

    Bundles a ``HistoryProvider`` and a ``CompactionStrategy`` together so
    callers don't have to pass them as two separate arguments.  The agent
    unpacks them into a running ``AgentContext`` during ``__init__``.

    Usage::

        # Explicit
        context = ContextConfig(
            InMemoryHistoryProvider(),
            [SlidingWindowCompaction(max_messages=40)],
        )
        agent = ReActAgent("bot", runtime, model=client, context=context)

        # Default (in-memory, sliding-window 100)
        context = ContextConfig.default()

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
    def default(cls) -> ContextConfig:
        """Return an in-memory context with default sliding-window compaction."""
        from ravi.agents.context.history import InMemoryHistoryProvider

        return cls(InMemoryHistoryProvider())


class AgentContext:
    """Concrete implementation of ``AgentContextProtocol`` for in-process use.

    Wraps a ``HistoryProvider`` and a ``CompactionStrategy`` into the full
    runtime context that ``ReActAgent`` drives.  All history reads and writes
    are scoped to ``session_id`` so one agent instance can participate in
    multiple sequential runs without history leaking between them.

    Construct via ``ContextConfig`` or directly::

        ctx = AgentContext(agent_id, InMemoryHistoryProvider(), SlidingWindowCompaction())
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

    async def get_prompt_window(self, session_id: str) -> list[Message]:
        """Return the compacted history as a prompt window for *session_id*."""
        raw = await self._history.get_messages(self._agent_id, session_id=session_id)
        return await self._compaction.compact(raw)


__all__ = ["AgentContextProtocol", "AgentContext", "ContextConfig"]
