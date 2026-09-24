"""ToolResultCompactionStrategy — truncates verbose tool result content."""

from __future__ import annotations

from substrate.kernel.core.content import ChatMessage, Role, TextBlock, ToolResultBlock


class ToolResultCompactionStrategy:
    """Truncates tool result content blocks that exceed *max_chars*."""

    def __init__(self, max_chars: int = 500) -> None:
        self._max = max_chars

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        result: list[ChatMessage] = []
        for msg in raw_history:
            if msg.role != Role.TOOL:
                result.append(msg)
                continue

            new_content, changed = self._compact_tool_message(msg.content)
            if not changed:
                result.append(msg)
            else:
                result.append(
                    ChatMessage(role=Role.TOOL, content=new_content, name=msg.name)
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
        # Only the text is shortened: images, data and any other block the tool
        # returned are kept exactly as they were.
        others = [b for b in block.content if not isinstance(b, TextBlock)]
        return ToolResultBlock(
            call_id=block.call_id,
            name=block.name,
            content=[TextBlock(text=truncated), *others],
            is_error=False,
        )


__all__ = ["ToolResultCompactionStrategy"]
