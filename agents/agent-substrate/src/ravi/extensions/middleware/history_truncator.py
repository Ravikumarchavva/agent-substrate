"""History context window truncator/optimizer middleware.

Prunes prompt history (oldest messages) during the LLM_CALL stage when
the message count exceeds configured limits.
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any

from ravi.kernel.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.kernel.messages.client_messages import SystemMessage

logger = setup_logging()


class HistoryTruncatorMiddleware(BaseMiddleware):
    """Truncates oldest history messages to prevent context window overflow.

    Keeps any SystemMessages intact, while dropping oldest non-system messages
    from the history until the total count fits within max_messages.
    """

    def __init__(
        self,
        *,
        name: str = "history_truncator",
        max_messages: int = 30,
    ) -> None:
        super().__init__(name)
        self.max_messages = max_messages

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        if ctx.stage != MiddlewareStage.LLM_CALL:
            return ctx

        messages = ctx.metadata.get("messages")
        if not messages or not isinstance(messages, list):
            return ctx

        if len(messages) <= self.max_messages:
            return ctx

        # Prune down to max_messages
        # We want to preserve the system instructions (usually the first message)
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        other_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

        # Determine how many other messages we can keep
        allowed_others = self.max_messages - len(system_msgs)
        if allowed_others < 0:
            allowed_others = 0

        # Keep the latest `allowed_others`
        pruned_others = other_msgs[-allowed_others:] if allowed_others > 0 else []

        # Re-assemble the messages
        pruned_messages = system_msgs + pruned_others
        ctx.metadata["messages"] = pruned_messages
        ctx.metadata["_original_message_count"] = len(messages)
        ctx.metadata["_pruned_message_count"] = len(pruned_messages)

        logger.debug(
            f"HistoryTruncatorMiddleware: pruned messages from {len(messages)} to {len(pruned_messages)}"
        )
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return result
