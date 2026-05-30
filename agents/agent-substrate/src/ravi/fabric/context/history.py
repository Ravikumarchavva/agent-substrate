from __future__ import annotations

from typing import Protocol

from ravi.kernel import AgentId, Message


class HistoryProvider(Protocol):
    """Durable storage for a single agent's raw message log.

    Stores every message the agent perceives or emits.  Does not
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


class InMemoryHistoryProvider:
    """Lightweight in-memory HistoryProvider for local dev and tests."""

    def __init__(self) -> None:
        self._store: dict[AgentId, list[Message]] = {}

    async def append(self, agent_id: AgentId, message: Message) -> None:
        self._store.setdefault(agent_id, []).append(message)

    async def get_messages(
        self,
        agent_id: AgentId,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Message]:
        msgs = list(self._store.get(agent_id, []))
        if offset is not None:
            msgs = msgs[offset:]
        if limit is not None:
            msgs = msgs[:limit]
        return msgs

    async def clear(self, agent_id: AgentId) -> None:
        self._store.pop(agent_id, None)
