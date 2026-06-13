"""TruncationStrategy — hard backstop by message count or character budget."""

from __future__ import annotations

from ravi.kernel.core.content import ChatMessage, TextBlock, ToolResultBlock


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
            history = history[-self._max_messages :]

        if self._max_chars is not None:
            history = self._truncate_by_chars(history)

        return history

    def _truncate_by_chars(self, history: list[ChatMessage]) -> list[ChatMessage]:
        kept: list[ChatMessage] = []
        total = 0
        for msg in reversed(history):
            chars = _estimate_chars(msg)
            if total + chars > self._max_chars:  # type: ignore[operator]
                break
            kept.append(msg)
            total += chars
        return list(reversed(kept))


def _estimate_chars(msg: ChatMessage) -> int:
    total = 0
    for block in msg.content:
        if isinstance(block, TextBlock):
            total += len(block.text)
        elif isinstance(block, ToolResultBlock):
            for inner in block.content:
                if isinstance(inner, TextBlock):
                    total += len(inner.text)
    return total


__all__ = ["TruncationStrategy"]
