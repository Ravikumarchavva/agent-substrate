"""HistoryProvider re-export + InMemoryHistoryProvider concrete impl."""

from __future__ import annotations

from ravi.kernel import AgentId, Message
from ravi.kernel.history import HistoryProvider


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


__all__ = ["HistoryProvider", "InMemoryHistoryProvider"]
