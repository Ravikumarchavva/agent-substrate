"""AgentContext (protocol impl) and ContextConfig (user-facing config bag)."""

from __future__ import annotations

import logging

from substrate.context.protocols import ContextBuilder
from substrate.types.supervision import HistoryRetention
from substrate.types.content import ChatMessage
from substrate.types.identity import Actor
from substrate.stores.threads import ThreadStore
from substrate.context.builder import DefaultContextBuilder
from substrate.context.compaction.coordinator import CompactionCoordinator
from substrate.context.compaction.pipeline import CompactionPipeline
from substrate.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.context.history import project_messages

logger = logging.getLogger(__name__)


class ContextConfig:
    """User-facing config bag — pass to agent constructors via ``context=...``.

    Bundles a ``ThreadStore``, a ``CompactionPipeline``, and a
    ``HistoryRetention`` policy together so callers don't have to pass them
    as separate arguments.

    ``retention`` controls what happens to history after a run completes:
    - ``PERMANENT`` (default) — kept forever; suitable for user-facing agents.
    - ``RUN`` — deleted after the run ends; for transient sub-agents.
    - ``NONE`` — never written; stateless workers.

    Pass a :class:`CompactionPipeline` configured with one or more strategies::

        from substrate.context import CompactionPipeline, ToolResultCompactionStrategy, SlidingWindowCompaction

        ctx = ContextConfig(
            LocalFilesystemThreadStore(),
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
        history: ThreadStore,
        pipeline: CompactionPipeline | None = None,
        *,
        retention: HistoryRetention = HistoryRetention.PERMANENT,
        builder: ContextBuilder | None = None,
        coordinator: CompactionCoordinator | None = None,
    ) -> None:
        self.history = history
        self.retention = retention
        self.pipeline: CompactionPipeline = pipeline or CompactionPipeline(
            [SlidingWindowCompaction()]
        )
        self.builder = builder or DefaultContextBuilder()
        self.coordinator = coordinator

        if retention == HistoryRetention.PERMANENT:
            ttl = getattr(history, "_ttl", None)
            if isinstance(ttl, int) and ttl > 0:
                logger.warning(
                    "ContextConfig: retention=PERMANENT with a TTL'd history "
                    "provider (%s, ttl=%ds) — history will silently expire "
                    "after %ds of inactivity. Use a durable provider "
                    "directly, or lower retention to RUN/NONE if that's "
                    "actually intended.",
                    type(history).__name__,
                    ttl,
                    ttl,
                )

    @classmethod
    def default(cls) -> "ContextConfig":
        """Return a durable local filesystem context with default sliding-window compaction."""
        from substrate.stores.local.threads import LocalFilesystemThreadStore

        return cls(LocalFilesystemThreadStore())


class AgentContext:
    """Concrete implementation of ``AgentContextProtocol`` for in-process use.

    Wraps a ``ThreadStore`` and a ``CompactionPipeline`` into the full
    runtime context that agents drive. All history reads and writes are
    scoped to ``session_id`` so one agent instance can participate in
    multiple sequential runs without history leaking between them.
    """

    def __init__(
        self,
        agent_id: Actor,
        history: ThreadStore,
        pipeline: CompactionPipeline,
        builder: ContextBuilder | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._history = history
        self._pipeline = pipeline
        self._builder = builder or DefaultContextBuilder()

    @property
    def agent_id(self) -> Actor:
        return self._agent_id

    @property
    def history(self) -> ThreadStore:
        return self._history

    async def get_prompt_window(
        self, session_id: str, *, branch_id: str = "main"
    ) -> list[ChatMessage]:
        """The branch's history as LLM-ready ChatMessages."""
        return await project_messages(
            self._history, session_id, branch_id=branch_id, builder=self._builder
        )


__all__ = ["AgentContext", "ContextConfig"]
