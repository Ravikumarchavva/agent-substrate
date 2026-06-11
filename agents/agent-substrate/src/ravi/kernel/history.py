"""History storage contract — ChatMessage-based, keyed by (agent_id, session_id, run_id)."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.content import ChatMessage
from ravi.kernel.identity import AgentId


class HistoryProvider(Protocol):
    """Durable storage for an agent's conversation transcript, scoped by session.

    Messages are ``ChatMessage`` (conversation turns), not routing envelopes.
    Keys are ``(agent_id, session_id)`` for retrieval and compaction;
    ``run_id`` is recorded with every message to support run-scoped cleanup.

    Session / run relationship:
    - ``session_id`` — the conversation thread (long-lived; many runs).
      History is always primarily keyed by ``session_id``.
    - ``run_id`` — one execution tree (short-lived; one run() call).
      ``clear_run`` deletes only messages from a specific run without
      touching other runs in the same session — enabling ``HistoryRetention.RUN``
      without destroying cross-run context.

    Does not summarise or compact — that is ``CompactionStrategy``'s job.
    """

    async def append(
        self,
        agent_id: AgentId,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        """Append *message* to *agent_id*'s history for *session_id*.

        ``run_id`` tags the message so ``clear_run`` can remove only
        messages from a specific run.
        """
        ...

    async def append_many(
        self,
        agent_id: AgentId,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        """Append multiple messages in one atomic write where possible."""
        ...

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        """Return the chronological message history for *agent_id* in *session_id*."""
        ...

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        """Delete all history for *agent_id* in *session_id* (all runs)."""
        ...

    async def clear_run(
        self, agent_id: AgentId, *, session_id: str, run_id: str
    ) -> None:
        """Delete only messages belonging to *run_id* within *session_id*.

        Used by ``HistoryRetention.RUN`` to clean up transient subagent
        history after a run completes without touching the session's
        cross-run context.
        """
        ...
