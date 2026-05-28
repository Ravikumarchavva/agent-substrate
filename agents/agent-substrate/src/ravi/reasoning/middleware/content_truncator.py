"""Content truncation middleware.

Truncates tool output text before it flows back into the LLM context
window, preventing context overflow on large tool results.
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any

from ravi.kernel.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.kernel.messages.content import TextBlock

logger = setup_logging()


class ContentTruncatorMiddleware(BaseMiddleware):
    """Truncates long tool results to fit the LLM context window.

    Only runs during ``TOOL_EXECUTION`` stage in ``after()``.
    """

    def __init__(
        self,
        *,
        name: str = "content_truncator",
        max_chars: int = 50_000,
        suffix: str = "\n\n[...truncated...]",
    ) -> None:
        super().__init__(name)
        self.max_chars = max_chars
        self.suffix = suffix

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        if ctx.stage != MiddlewareStage.TOOL_EXECUTION:
            return result

        # ToolResult has .content which is list[dict] with text blocks
        content = getattr(result, "content", None)
        if not content or not isinstance(content, list):
            return result

        truncated = False
        for i, block in enumerate(content):
            if isinstance(block, TextBlock):
                if len(block.text) > self.max_chars:
                    content[i] = TextBlock(
                        text=block.text[: self.max_chars] + self.suffix
                    )
                    truncated = True

        if truncated:
            logger.debug(
                f"ContentTruncator: truncated tool result to {self.max_chars} chars"
            )

        return result
