"""AgentContext (protocol impl) and ContextConfig (user-facing config bag)."""

from __future__ import annotations

from substrate.kernel.core.content import ChatMessage
from substrate.kernel.agent.context import AgentContextProtocol
from substrate.kernel.agent.supervision import HistoryRetention
from substrate.kernel.storage.history import HistoryProvider
from substrate.kernel.core.identity import AgentId
from substrate.logger import setup_logging
from .compaction import SlidingWindowCompaction, CompactionPipeline

logger = setup_logging()


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

        from substrate.agents.context import CompactionPipeline, ToolResultCompactionStrategy, SlidingWindowCompaction

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
        if retention == HistoryRetention.PERMANENT:
            ttl = getattr(history, "_ttl", None)
            if isinstance(ttl, int) and ttl > 0:
                logger.warning(
                    "ContextConfig: retention=PERMANENT with a TTL'd history "
                    "provider (%s, ttl=%ds) — history will silently expire "
                    "after %ds of inactivity. Wrap it (e.g. "
                    "CachedHistoryProvider) or use a durable provider "
                    "directly, or lower retention to RUN/NONE if that's "
                    "actually intended.",
                    type(history).__name__,
                    ttl,
                    ttl,
                )

    @classmethod
    def default(cls) -> "ContextConfig":
        """Return an in-memory context with default sliding-window compaction."""
        from substrate.agents.context.history import InMemoryHistoryProvider

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
