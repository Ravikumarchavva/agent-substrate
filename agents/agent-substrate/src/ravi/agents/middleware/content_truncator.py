from __future__ import annotations

from typing import Any

from ravi.kernel import TextBlock
from ravi.logger import setup_logging
from ravi.agents.middleware._contracts import MiddlewareContext, MiddlewareStage

logger = setup_logging()


class ContentTruncatorMiddleware:
    """Truncates long tool results to fit the LLM context window."""

    def __init__(
        self, *, max_chars: int = 50_000, suffix: str = "\n\n[...truncated...]"
    ) -> None:
        self.max_chars = max_chars
        self.suffix = suffix

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        if ctx.stage != MiddlewareStage.TOOL_EXECUTION:
            return result
        content = getattr(result, "content", None)
        if not content or not isinstance(content, list):
            return result
        truncated = False
        for i, block in enumerate(content):
            if isinstance(block, TextBlock) and len(block.text) > self.max_chars:
                content[i] = TextBlock(text=block.text[: self.max_chars] + self.suffix)
                truncated = True
        if truncated:
            logger.debug(
                "ContentTruncator: truncated tool result to %d chars", self.max_chars
            )
        return result
