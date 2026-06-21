"""AgentContext (protocol impl) and ContextConfig (user-facing config bag)."""

from __future__ import annotations

from agent_substrate.kernel.core.content import ChatMessage
from agent_substrate.kernel.agent.context import AgentContextProtocol
from agent_substrate.kernel.agent.supervision import HistoryRetention
from agent_substrate.kernel.storage.history import HistoryProvider
from agent_substrate.kernel.core.identity import AgentId
from .compaction import SlidingWindowCompaction, CompactionPipeline


class ContextConfig:
    """User-facing config bag — pass to agent constructors via ``context=...``.

    Bundles a ``HistoryProvider``, a ``CompactionPipeline``, and a
    ``HistoryRetention`` policy together so callers don't have to pass them
    as separate arguments.

    ``retention`` controls what happens to history after a run completes:
    - ``PERMANENT`` (default) — kept forever; suitable for user-facing agents.
    - ``RUN`` — deleted after the run ends; for transient sub-agents.
    - ``NONE`` — never written; stateless workers.

    Pass a :class:`CompactionPipeline` configured with one or more strategies::

        from agent_substrate.agents.context import CompactionPipeline, ToolResultCompactionStrategy, SlidingWindowCompaction

        ctx = ContextConfig(
            InMemoryHistoryProvider(),
            CompactionPipeline([
                ToolResultCompactionStrategy(),
                SlidingWindowCompaction(max_messages=40),
            ]),
            retention=HistoryRetention.RUN,
        )
        agent = ReActAgent("bot", model=client, context=ctx)
    """

    def __init__(
        self,
        history: HistoryProvider,
        pipeline: CompactionPipeline | None = None,
        *,
        retention: HistoryRetention = HistoryRetention.PERMANENT,
    ) -> None:
        self.history = history
        self.retention = retention
        self.pipeline: CompactionPipeline = pipeline or CompactionPipeline(
            [SlidingWindowCompaction()]
        )

    @classmethod
    def default(cls) -> "ContextConfig":
        """Return an in-memory context with default sliding-window compaction."""
        from agent_substrate.agents.context.history import InMemoryHistoryProvider

        return cls(InMemoryHistoryProvider())


class AgentContext:
    """Concrete implementation of ``AgentContextProtocol`` for in-process use.

    Wraps a ``HistoryProvider`` and a ``CompactionPipeline`` into the full
    runtime context that agents drive.  All history reads and writes are
    scoped to ``session_id`` so one agent instance can participate in
    multiple sequential runs without history leaking between them.
    """

    def __init__(
        self,
        agent_id: AgentId,
        history: HistoryProvider,
        pipeline: CompactionPipeline,
    ) -> None:
        self._agent_id = agent_id
        self._history = history
        self._pipeline = pipeline

    @property
    def agent_id(self) -> AgentId:
        return self._agent_id

    @property
    def history(self) -> HistoryProvider:
        return self._history

    async def get_prompt_window(self, session_id: str) -> list[ChatMessage]:
        """Return the compacted history as ChatMessages for LLM generation."""
        raw = await self._history.get_messages(self._agent_id, session_id=session_id)
        return await self._pipeline.compact(raw)


__all__ = ["AgentContextProtocol", "AgentContext", "ContextConfig"]
