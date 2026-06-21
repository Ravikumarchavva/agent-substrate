"""SlidingWindowCompaction — retains only the N most-recent messages."""

from __future__ import annotations

from agent_substrate.kernel.core.content import ChatMessage


class SlidingWindowCompaction:
    """Drops oldest messages once history exceeds *max_messages*."""

    def __init__(self, max_messages: int = 100) -> None:
        self.max_messages = max_messages

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        if len(raw_history) <= self.max_messages:
            return raw_history
        return raw_history[-self.max_messages :]


__all__ = ["SlidingWindowCompaction"]
