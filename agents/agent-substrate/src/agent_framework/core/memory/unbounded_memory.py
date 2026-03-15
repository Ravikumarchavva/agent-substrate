"""Unbounded memory implementation that stores all messages."""
from __future__ import annotations

from typing import List, Optional

from .base_memory import BaseMemory
from agent_framework.core.messages.base_message import BaseClientMessage


class UnboundedMemory(BaseMemory):
    """Simple in-process memory that stores all messages without limit.

    All methods are async to conform to the Memory ABC, but since this is
    in-process there is no actual I/O — the async is trivial.
    """

    def __init__(self) -> None:
        self._messages: List[BaseClientMessage] = []

    async def add_message(self, message: BaseClientMessage) -> None:
        """Add a message to memory."""
        self._messages.append(message)

    async def get_messages(self, limit: Optional[int] = None) -> List[BaseClientMessage]:
        """Retrieve messages from memory.

        Args:
            limit: If provided, return only the last N messages.
        """
        if limit is None:
            return self._messages.copy()
        return self._messages[-limit:] if limit > 0 else []

    async def clear(self) -> None:
        """Clear all messages."""
        self._messages = []

    async def get_token_count(self) -> int:
        """Approximate token count (4 chars ≈ 1 token heuristic)."""
        total = 0
        for msg in self._messages:
            content = msg.content
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            else:
                text = str(content)
            total += len(text) // 4
        return total

    def __repr__(self) -> str:
        return f"<UnboundedMemory(messages={len(self._messages)})>"