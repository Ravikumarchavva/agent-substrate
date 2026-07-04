from __future__ import annotations

from typing import Callable, Awaitable, ClassVar

from substrate.logger import setup_logging
from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.kernel.agent.middleware import MiddlewareStage

logger = setup_logging()


class ContentTruncatorMiddleware:
    """Truncates long tool results to fit the LLM context window."""

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TOOL})

    def __init__(
        self, *, max_chars: int = 50_000, suffix: str = "\n\n[...truncated...]"
    ) -> None:
        self.max_chars = max_chars
        self.suffix = suffix

    async def process(
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        await call_next()

        if context.tool_result is None or len(context.tool_result.text) <= self.max_chars:
            return

        # InvocationResult is frozen — rebuild rather than mutate in place.
        context.tool_result = context.tool_result.model_copy(
            update={"text": context.tool_result.text[: self.max_chars] + self.suffix}
        )
        logger.debug(
            "ContentTruncator: truncated tool result to %d chars", self.max_chars
        )
