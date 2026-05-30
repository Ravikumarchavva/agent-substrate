"""HistoryProvider — the single conversation-history contract.

Every conversation-history backend implements one vocabulary:
``save_messages`` / ``load_messages`` / ``count_messages`` / ``clear_session``,
all keyed by ``session_id``.  This lets in-process, cached (Redis, Memgraph,
FalkorDB, …) and persistent (Postgres, Neo4j, MySQL, …) backends be
interchangeable and composable.

This project is async-first: all methods are ``async def``.  In-process
stores are trivially async (no I/O); remote stores are properly async.

This contract covers *conversation history* — the message content fed back
to the LLM.  Provenance metadata (who created a message, what caused it) is
a separate concern handled by :mod:`ravi.kernel.memory._lineage`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ravi.kernel.messages.base_message import BaseClientMessage


class HistoryProvider(ABC):
    """Multi-session conversation-history store.

    A backend stores messages for many sessions, each addressed by an opaque
    ``session_id``.  ``connect`` / ``disconnect`` default to no-ops so purely
    in-process providers need not override them.
    """

    async def connect(self) -> None:
        """Open any backing connection. No-op by default."""
        return None

    async def disconnect(self) -> None:
        """Close any backing connection. No-op by default."""
        return None

    @abstractmethod
    async def save_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> int:
        """Append *messages* to *session_id*. Returns the number written."""
        ...

    @abstractmethod
    async def load_messages(
        self, session_id: str, *, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        """Return a session's messages, oldest first.

        Args:
            session_id: Session to read.
            limit: If given, return only the last *limit* messages.
        """
        ...

    @abstractmethod
    async def count_messages(self, session_id: str) -> int:
        """Return the number of stored messages for *session_id*."""
        ...

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """Erase all stored messages for *session_id*."""
        ...

    async def __aenter__(self) -> "HistoryProvider":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()


class CachedHistoryProvider(HistoryProvider):
    """Base for fast, volatile backends (Redis default; Memgraph/FalkorDB extend this).

    Adds TTL semantics. Subclasses persist with an expiry and may trim history
    to ``max_messages`` to bound memory.
    """

    def __init__(self, *, ttl: int = 3600, max_messages: int = 200) -> None:
        if ttl < 0:
            raise ValueError("ttl must be >= 0 (0 disables expiry)")
        if max_messages < 1:
            raise ValueError("max_messages must be >= 1")
        self._ttl = ttl
        self._max_messages = max_messages

    @property
    def ttl(self) -> int:
        return self._ttl

    @property
    def max_messages(self) -> int:
        return self._max_messages

    @abstractmethod
    async def refresh_ttl(self, session_id: str) -> None:
        """Reset the expiry clock on *session_id*."""
        ...


class PersistentHistoryProvider(HistoryProvider):
    """Base for durable backends (Postgres default; Neo4j/MySQL extend this).

    Marker base — durable stores have no TTL and never silently drop history.
    """
