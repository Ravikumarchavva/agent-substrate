from __future__ import annotations

import logging
from typing import Awaitable, Callable, ClassVar

from substrate.middleware.stage import MiddlewareStage
from substrate.middleware._contracts import MiddlewareContext

logger = logging.getLogger(__name__)


class HistoryTruncatorMiddleware:
    """Truncates oldest history messages to prevent context window overflow.

    Keeps any system messages intact while dropping the oldest non-system messages.
    """

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.CHAT})

    def __init__(self, *, max_messages: int = 30) -> None:
        self.max_messages = max_messages

    async def process(
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        messages = context.messages or []
        if len(messages) > self.max_messages:
            system_msgs = [m for m in messages if getattr(m, "role", None) == "system"]
            other_msgs = [m for m in messages if getattr(m, "role", None) != "system"]
            allowed_others = max(0, self.max_messages - len(system_msgs))
            pruned_others = other_msgs[-allowed_others:] if allowed_others > 0 else []
            pruned = system_msgs + pruned_others
            context.messages = pruned
            context.metadata["_original_message_count"] = len(messages)
            context.metadata["_pruned_message_count"] = len(pruned)
            logger.debug(
                "HistoryTruncator: pruned messages from %d to %d",
                len(messages),
                len(pruned),
            )

        await call_next()
