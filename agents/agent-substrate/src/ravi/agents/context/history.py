"""HistoryProvider re-export + InMemoryHistoryProvider concrete impl."""

from __future__ import annotations

from ravi.kernel import AgentId, Message
from ravi.kernel.history import HistoryProvider


class InMemoryHistoryProvider:
    """Lightweight in-memory HistoryProvider for local dev and tests.

    Messages are keyed by ``(agent_id, session_id)`` so that multiple sessions
    for the same agent don't bleed into each other and ``clear()`` only
    deletes the specified session. Within a session messages accumulate across
    runs — enabling cross-turn memory for PERMANENT-retention subagents.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[AgentId, str], list[Message]] = {}

    async def append(self, agent_id: AgentId, message: Message, *, session_id: str) -> None:
        self._store.setdefault((agent_id, session_id), []).append(message)

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Message]:
        msgs = list(self._store.get((agent_id, session_id), []))
        if offset is not None:
            msgs = msgs[offset:]
        if limit is not None:
            msgs = msgs[:limit]
        return msgs

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        self._store.pop((agent_id, session_id), None)


__all__ = ["HistoryProvider", "InMemoryHistoryProvider"]
