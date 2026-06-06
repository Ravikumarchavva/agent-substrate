"""ToolResultCompactionStrategy — truncates verbose tool result content."""

from __future__ import annotations

from ravi.kernel import Message
from ravi.kernel.content import ChatMessage, TextBlock, ToolResultBlock


class ToolResultCompactionStrategy:
    """Truncates tool result content blocks that exceed *max_chars*.

    Aggressiveness: Low
    Preserves context: High — tool calls and summaries remain; only verbose
        output text is shortened.
    Requires LLM: No

    Best for reclaiming space from tool output that is long but whose full
    text is no longer needed (e.g. a web page fetched 10 turns ago).

    Errors (``is_error=True``) are never truncated — their full content is
    preserved so the agent can reason about what went wrong.

    Args:
        max_chars: Maximum characters to keep in a single tool result's text.
                   Results under this limit are untouched.
    """

    def __init__(self, max_chars: int = 500) -> None:
        self._max = max_chars

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        result: list[Message] = []
        for msg in raw_history:
            if not isinstance(msg.payload, ChatMessage) or msg.payload.role != "tool":
                result.append(msg)
                continue

            new_content, changed = self._compact_tool_message(msg.payload.content)
            if not changed:
                result.append(msg)
            else:
                new_cm = ChatMessage(role="tool", content=new_content)
                result.append(
                    Message(
                        target=msg.target,
                        payload=new_cm,
                        sender=msg.sender,
                        correlation_id=msg.correlation_id,
                        causation_id=msg.causation_id,
                    )
                )
        return result

    def _compact_tool_message(
        self, content: list
    ) -> tuple[list, bool]:
        new_content = []
        changed = False
        for block in content:
            if isinstance(block, ToolResultBlock) and not block.is_error:
                compacted = self._truncate_result(block)
                if compacted is not block:
                    changed = True
                new_content.append(compacted)
            else:
                new_content.append(block)
        return new_content, changed

    def _truncate_result(self, block: ToolResultBlock) -> ToolResultBlock:
        text_blocks = [b for b in block.content if isinstance(b, TextBlock)]
        total = sum(len(b.text) for b in text_blocks)
        if total <= self._max:
            return block

        full_text = "\n".join(b.text for b in text_blocks)
        truncated = (
            full_text[: self._max]
            + f"\n… [{total - self._max} chars truncated]"
        )
        return ToolResultBlock(
            call_id=block.call_id,
            content=[TextBlock(text=truncated)],
            is_error=False,
        )


__all__ = ["ToolResultCompactionStrategy"]
