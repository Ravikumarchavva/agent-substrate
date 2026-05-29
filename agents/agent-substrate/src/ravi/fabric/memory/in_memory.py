"""InMemoryHistoryProvider — in-process RAM history store.

The reference :class:`HistoryProvider`.  Stores every session's messages in a
plain dict; trivially async (no I/O).  This is the default backend when no
cached/persistent store is wired.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ravi.kernel.memory.history_provider import HistoryProvider
from ravi.kernel.messages.base_message import BaseClientMessage


class InMemoryHistoryProvider(HistoryProvider):
    """Multi-session conversation history held in process memory.

    Stores all messages without limit, keyed by ``session_id``.  Lost on
    process exit — use a cached or persistent provider for durability.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, List[BaseClientMessage]] = {}

    async def save_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> int:
        if not messages:
            return 0
        self._sessions.setdefault(session_id, []).extend(messages)
        return len(messages)

    async def load_messages(
        self, session_id: str, *, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        msgs = self._sessions.get(session_id, [])
        if limit is None:
            return msgs.copy()
        return msgs[-limit:] if limit > 0 else []

    async def count_messages(self, session_id: str) -> int:
        return len(self._sessions.get(session_id, []))

    async def clear_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._sessions.values())
        return (
            f"<InMemoryHistoryProvider(sessions={len(self._sessions)}, "
            f"messages={total})>"
        )
