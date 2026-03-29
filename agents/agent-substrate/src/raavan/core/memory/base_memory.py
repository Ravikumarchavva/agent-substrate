"""Memory ABC — single async interface.

This project is async-first from the start.  There is no separate sync /
async split: all memory implementations use ``async def`` methods.
In-process stores (``UnboundedMemory``) are trivially async (no I/O);
remote stores (``RedisMemory``) are properly async.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from raavan.core.messages.base_message import BaseClientMessage


class BaseMemory(ABC):
    """Async memory interface for all agent memory implementations."""

    @abstractmethod
    async def add_message(self, message: BaseClientMessage) -> None:
        """Persist *message* to the backing store."""
        ...

    @abstractmethod
    async def get_messages(
        self, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        """Retrieve messages.

        Args:
            limit: If provided, return only the last *limit* messages.
        Returns:
            List of stored messages (oldest first).
        """
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Erase all stored messages."""
        ...

    @abstractmethod
    async def get_token_count(self) -> int:
        """Return approximate token count of stored messages."""
        ...

    async def size(self) -> int:
        """Return message count."""
        return len(await self.get_messages())
