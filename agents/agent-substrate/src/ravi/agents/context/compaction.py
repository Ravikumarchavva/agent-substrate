"""CompactionStrategy re-export + SlidingWindowCompaction concrete impl."""

from __future__ import annotations

from ravi.kernel import Message
from ravi.kernel.context import CompactionStrategy


class SlidingWindowCompaction:
    """Retains only the most recent *max_messages* messages."""

    def __init__(self, max_messages: int = 100) -> None:
        self.max_messages = max_messages

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        if len(raw_history) <= self.max_messages:
            return raw_history
        return raw_history[-self.max_messages :]


__all__ = ["CompactionStrategy", "SlidingWindowCompaction"]
