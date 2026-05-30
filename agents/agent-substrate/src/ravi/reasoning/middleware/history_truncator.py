from __future__ import annotations

from typing import Any

from ravi.logger import setup_logging
from ravi.reasoning.middleware._contracts import MiddlewareContext, MiddlewareStage

logger = setup_logging()


class HistoryTruncatorMiddleware:
    """Truncates oldest history messages to prevent context window overflow.

    Keeps any system messages intact while dropping the oldest non-system messages.
    Messages are expected to be objects with a ``role`` attribute.
    """

    def __init__(self, *, max_messages: int = 30) -> None:
        self.max_messages = max_messages

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        if ctx.stage != MiddlewareStage.LLM_CALL:
            return ctx
        messages = ctx.metadata.get("messages")
        if not messages or not isinstance(messages, list):
            return ctx
        if len(messages) <= self.max_messages:
            return ctx

        system_msgs = [m for m in messages if getattr(m, "role", None) == "system"]
        other_msgs = [m for m in messages if getattr(m, "role", None) != "system"]
        allowed_others = max(0, self.max_messages - len(system_msgs))
        pruned_others = other_msgs[-allowed_others:] if allowed_others > 0 else []
        pruned = system_msgs + pruned_others
        ctx.metadata["messages"] = pruned
        ctx.metadata["_original_message_count"] = len(messages)
        ctx.metadata["_pruned_message_count"] = len(pruned)
        logger.debug(
            "HistoryTruncator: pruned messages from %d to %d", len(messages), len(pruned)
        )
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return result
