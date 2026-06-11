from __future__ import annotations

from typing import Callable, Awaitable

from ravi.kernel import TextBlock
from ravi.logger import setup_logging
from ravi.agents.middleware._contracts import FunctionContext

logger = setup_logging()


class ContentTruncatorMiddleware:
    """Truncates long tool results to fit the LLM context window."""

    def __init__(
        self, *, max_chars: int = 50_000, suffix: str = "\n\n[...truncated...]"
    ) -> None:
        self.max_chars = max_chars
        self.suffix = suffix

    async def process(
        self, context: FunctionContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        await call_next()

        if context.result is None:
            return

        content = getattr(context.result, "content", None)
        if not content or not isinstance(content, list):
            return

        truncated = False
        for i, block in enumerate(content):
            if isinstance(block, TextBlock) and len(block.text) > self.max_chars:
                content[i] = TextBlock(text=block.text[: self.max_chars] + self.suffix)
                truncated = True

        if truncated:
            logger.debug(
                "ContentTruncator: truncated tool result to %d chars", self.max_chars
            )
