"""SelectiveToolCallCompactionStrategy — removes old tool-call/result pairs."""

from __future__ import annotations

from ravi.kernel import Message
from ravi.kernel.content import ChatMessage, ToolUseBlock


class SelectiveToolCallCompactionStrategy:
    """Drops old tool-call + tool-result groups, keeping recent ones intact.

    Aggressiveness: Low–Medium
    Preserves context: Medium — non-tool messages are untouched; only tool
        call/result pairs beyond the keep window are dropped.
    Requires LLM: No

    A "group" is one assistant message containing ToolUseBlock(s) plus the
    immediately following tool-result message(s). Groups are identified by
    scanning forward; if the assistant turn contains both text and tool calls,
    the entire message is removed along with its results.

    Best for long agentic sessions where old tool results (searches, API
    responses) have been incorporated into later assistant turns and the raw
    results are no longer useful.

    Args:
        keep_recent_groups: Number of most-recent tool-call groups to keep.
    """

    def __init__(self, keep_recent_groups: int = 5) -> None:
        self._keep = keep_recent_groups

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        groups = self._find_groups(raw_history)
        if len(groups) <= self._keep:
            return raw_history

        remove = set()
        for start, end in groups[: len(groups) - self._keep]:
            remove.update(range(start, end + 1))

        return [msg for i, msg in enumerate(raw_history) if i not in remove]

    def _find_groups(self, history: list[Message]) -> list[tuple[int, int]]:
        """Return ``(start_index, end_index)`` for each tool-call group."""
        groups: list[tuple[int, int]] = []
        i = 0
        while i < len(history):
            msg = history[i]
            if self._is_tool_call_turn(msg):
                # Collect all consecutive tool-result messages that follow.
                j = i + 1
                while j < len(history) and self._is_tool_result_turn(history[j]):
                    j += 1
                groups.append((i, j - 1))
                i = j
            else:
                i += 1
        return groups

    @staticmethod
    def _is_tool_call_turn(msg: Message) -> bool:
        return (
            isinstance(msg.payload, ChatMessage)
            and msg.payload.role == "assistant"
            and any(isinstance(b, ToolUseBlock) for b in msg.payload.content)
        )

    @staticmethod
    def _is_tool_result_turn(msg: Message) -> bool:
        return isinstance(msg.payload, ChatMessage) and msg.payload.role == "tool"


__all__ = ["SelectiveToolCallCompactionStrategy"]
