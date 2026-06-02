"""History storage contract."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.identity import AgentId
from ravi.kernel.message import Message


class HistoryProvider(Protocol):
    """Durable storage for a single agent's raw message log, scoped by session.

    Messages are keyed by both ``agent_id`` and ``session_id`` (the conversation
    thread) so that:
    - A single agent can participate in multiple runs within the same session
      without history leaking between different sessions.
    - ``HistoryRetention.RUN`` can delete all messages for one session without
      touching the agent's history from other sessions.
    - ``run()`` can resume a crashed agent by reloading messages from a
      specific ``(agent_id, session_id)`` pair.
    - Subagents with ``HistoryRetention.PERMANENT`` accumulate history across
      many runs in the same session, enabling cross-turn memory.

    The ``session_id`` is the conversation thread (long-lived); the ``run_id``
    is the execution scope (short-lived, one ``run()`` call). They are separate:
    history is always scoped by ``session_id``, never by ``run_id``.

    Does not summarise or compact — that is ``CompactionStrategy``'s job.
    """

    async def append(self, agent_id: AgentId, message: Message, *, session_id: str) -> None:
        """Append *message* to *agent_id*'s history for *session_id*."""
        ...

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Message]:
        """Return the chronological message history for *agent_id* in *session_id*."""
        ...

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        """Delete all history for *agent_id* in *session_id*."""
        ...
