"""SlidingWindowCompaction — retains only the N most-recent messages."""

from __future__ import annotations

from substrate.types.content import ChatMessage
from substrate.context.compaction._window import drop_orphaned_tool_results


class SlidingWindowCompaction:
    """Drops oldest messages once history exceeds *max_messages*."""

    def __init__(self, max_messages: int = 100) -> None:
        self.max_messages = max_messages

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        if len(raw_history) <= self.max_messages:
            return raw_history
        return drop_orphaned_tool_results(raw_history[-self.max_messages :])


__all__ = ["SlidingWindowCompaction"]
