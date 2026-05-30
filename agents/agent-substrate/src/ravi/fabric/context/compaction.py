from __future__ import annotations

from typing import Protocol

from ravi.kernel import Message


class CompactionStrategy(Protocol):
    """Converts raw message history into a manageable LLM context window.

    Implementations might use sliding windows, token truncation, or
    background summarisation.
    """

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        """Return the optimised sequence ready for LLM encoding."""
        ...


class SlidingWindowCompaction:
    """Retains only the most recent *max_messages* messages."""

    def __init__(self, max_messages: int = 100) -> None:
        self.max_messages = max_messages

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        if len(raw_history) <= self.max_messages:
            return raw_history
        return raw_history[-self.max_messages:]
