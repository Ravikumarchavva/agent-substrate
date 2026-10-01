"""TruncationStrategy — hard backstop by message count or character budget."""

from __future__ import annotations

from substrate.agents.context.compaction._window import drop_orphaned_tool_results
from substrate.agents.context.tokens import estimate_message_chars
from substrate.kernel.core.content import ChatMessage


class TruncationStrategy:
    """Drops oldest messages until history fits within the configured limit."""

    def __init__(
        self,
        max_messages: int | None = None,
        max_chars: int | None = None,
    ) -> None:
        if max_messages is None and max_chars is None:
            raise ValueError("TruncationStrategy requires max_messages or max_chars")
        self._max_messages = max_messages
        self._max_chars = max_chars

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        history = raw_history

        if self._max_messages is not None and len(history) > self._max_messages:
            history = drop_orphaned_tool_results(history[-self._max_messages :])

        if self._max_chars is not None:
            history = self._truncate_by_chars(history)

        return history

    def _truncate_by_chars(self, history: list[ChatMessage]) -> list[ChatMessage]:
        kept: list[ChatMessage] = []
        total = 0
        for msg in reversed(history):
            chars = estimate_message_chars(msg)
            if total + chars > self._max_chars:  # type: ignore[operator]
                break
            kept.append(msg)
            total += chars
        return drop_orphaned_tool_results(list(reversed(kept)))


__all__ = ["TruncationStrategy"]
