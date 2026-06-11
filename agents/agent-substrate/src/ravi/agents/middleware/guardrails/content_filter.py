from __future__ import annotations

import re
from typing import Callable, Awaitable

from ravi.agents.middleware._contracts import AgentRunContext
from ravi.exceptions import MiddlewareTermination
from ravi.kernel.content import TextBlock


class ContentFilterMiddleware:
    """Block messages that match any pattern in a configurable blocklist."""

    def __init__(
        self,
        *,
        blocked_patterns: list[str] | None = None,
        blocked_keywords: list[str] | None = None,
    ):
        self.blocked_keywords = [kw.lower() for kw in (blocked_keywords or [])]
        self._compiled: list[re.Pattern[str]] = []
        for p in blocked_patterns or []:
            try:
                self._compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                raise ValueError(f"Invalid blocked_pattern regex '{p}': {e}") from e

    async def process(
        self, context: AgentRunContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        if not context.messages:
            await call_next()
            return

        last_msg = context.messages[-1]
        text = " ".join(b.text for b in last_msg.content if isinstance(b, TextBlock))

        text_lower = text.lower()
        for kw in self.blocked_keywords:
            if kw in text_lower:
                raise MiddlewareTermination(
                    f"ContentFilter: Blocked keyword detected: '{kw}'"
                )

        for pattern in self._compiled:
            match = pattern.search(text)
            if match:
                raise MiddlewareTermination(
                    f"ContentFilter: Blocked pattern matched: '{pattern.pattern}'"
                )

        await call_next()
