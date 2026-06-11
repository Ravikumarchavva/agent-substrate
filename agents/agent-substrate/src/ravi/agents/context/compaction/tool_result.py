"""ToolResultCompactionStrategy — truncates verbose tool result content."""

from __future__ import annotations

from ravi.kernel.content import ChatMessage, TextBlock, ToolResultBlock


class ToolResultCompactionStrategy:
    """Truncates tool result content blocks that exceed *max_chars*."""

    def __init__(self, max_chars: int = 500) -> None:
        self._max = max_chars

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        result: list[ChatMessage] = []
        for msg in raw_history:
            if msg.role != "tool":
                result.append(msg)
                continue

            new_content, changed = self._compact_tool_message(msg.content)
            if not changed:
                result.append(msg)
            else:
                result.append(
                    ChatMessage(role="tool", content=new_content, name=msg.name)
                )
        return result

    def _compact_tool_message(self, content: list) -> tuple[list, bool]:
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
            full_text[: self._max] + f"\n… [{total - self._max} chars truncated]"
        )
        return ToolResultBlock(
            call_id=block.call_id,
            name=block.name,
            content=[TextBlock(text=truncated)],
            is_error=False,
        )


__all__ = ["ToolResultCompactionStrategy"]
