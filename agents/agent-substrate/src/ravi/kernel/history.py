"""History storage contract."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.identity import AgentId
from ravi.kernel.message import Message


class HistoryProvider(Protocol):
    """Durable storage for a single agent's raw message log.

    Stores every message the agent perceives or emits. Does not
    summarise or compact — that is ``CompactionStrategy``'s job.
    """

    async def append(self, agent_id: AgentId, message: Message) -> None:
        """Append *message* to *agent_id*'s history."""
        ...

    async def get_messages(
        self,
        agent_id: AgentId,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Message]:
        """Return the chronological message history for *agent_id*."""
        ...

    async def clear(self, agent_id: AgentId) -> None:
        """Delete all history for *agent_id*."""
        ...
