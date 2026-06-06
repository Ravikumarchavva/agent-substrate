"""SlidingWindowStrategy — retains only the N most-recent messages."""

from __future__ import annotations

from ravi.kernel import Message


class SlidingWindowStrategy:
    """Drops oldest messages once history exceeds *max_messages*.

    Aggressiveness: High
    Preserves context: Low — oldest messages are lost entirely.
    Requires LLM: No

    Best for hard group-count limits where simplicity matters more than
    context preservation.
    """

    def __init__(self, max_messages: int = 100) -> None:
        self.max_messages = max_messages

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        if len(raw_history) <= self.max_messages:
            return raw_history
        return raw_history[-self.max_messages:]


__all__ = ["SlidingWindowStrategy"]
