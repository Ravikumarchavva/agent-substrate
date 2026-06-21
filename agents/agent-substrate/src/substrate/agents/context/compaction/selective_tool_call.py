"""SelectiveToolCallCompactionStrategy — removes old tool-call/result pairs."""

from __future__ import annotations

from substrate.kernel.core.content import ChatMessage, ToolUseBlock


class SelectiveToolCallCompactionStrategy:
    """Drops old tool-call + tool-result groups, keeping recent ones intact."""

    def __init__(self, keep_recent_groups: int = 5) -> None:
        self._keep = keep_recent_groups

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        groups = self._find_groups(raw_history)
        if len(groups) <= self._keep:
            return raw_history

        remove = set()
        for start, end in groups[: len(groups) - self._keep]:
            remove.update(range(start, end + 1))

        return [msg for i, msg in enumerate(raw_history) if i not in remove]

    def _find_groups(self, history: list[ChatMessage]) -> list[tuple[int, int]]:
        groups: list[tuple[int, int]] = []
        i = 0
        while i < len(history):
            msg = history[i]
            if self._is_tool_call_turn(msg):
                j = i + 1
                while j < len(history) and self._is_tool_result_turn(history[j]):
                    j += 1
                groups.append((i, j - 1))
                i = j
            else:
                i += 1
        return groups

    @staticmethod
    def _is_tool_call_turn(msg: ChatMessage) -> bool:
        return msg.role == "assistant" and any(
            isinstance(b, ToolUseBlock) for b in msg.content
        )

    @staticmethod
    def _is_tool_result_turn(msg: ChatMessage) -> bool:
        return msg.role == "tool"


__all__ = ["SelectiveToolCallCompactionStrategy"]
