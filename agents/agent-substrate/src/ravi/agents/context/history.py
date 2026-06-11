"""HistoryProvider re-export + InMemoryHistoryProvider concrete impl."""

from __future__ import annotations

from ravi.kernel.content import ChatMessage
from ravi.kernel.history import HistoryProvider
from ravi.kernel.identity import AgentId


class InMemoryHistoryProvider:
    """Lightweight in-memory HistoryProvider for local dev and tests.

    Messages are keyed by ``(agent_id, session_id)`` with run-level metadata
    stored alongside so ``clear_run`` can delete only one run's messages.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[AgentId, str], list[tuple[str, ChatMessage]]] = {}

    @staticmethod
    def _tag(message: ChatMessage, run_id: str) -> ChatMessage:
        if not run_id or message.metadata.get("run_id") == run_id:
            return message
        return message.model_copy(
            update={"metadata": {**message.metadata, "run_id": run_id}}
        )

    async def append(
        self,
        agent_id: AgentId,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        tagged = self._tag(message, run_id)
        self._store.setdefault((agent_id, session_id), []).append((run_id, tagged))

    async def append_many(
        self,
        agent_id: AgentId,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        bucket = self._store.setdefault((agent_id, session_id), [])
        bucket.extend((run_id, self._tag(m, run_id)) for m in messages)

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        pairs = self._store.get((agent_id, session_id), [])
        msgs = [m for _, m in pairs]
        if offset is not None:
            msgs = msgs[offset:]
        if limit is not None:
            msgs = msgs[:limit]
        return msgs

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        self._store.pop((agent_id, session_id), None)

    async def clear_run(
        self, agent_id: AgentId, *, session_id: str, run_id: str
    ) -> None:
        key = (agent_id, session_id)
        if key in self._store:
            self._store[key] = [
                (rid, m) for rid, m in self._store[key] if rid != run_id
            ]


__all__ = ["HistoryProvider", "InMemoryHistoryProvider"]
