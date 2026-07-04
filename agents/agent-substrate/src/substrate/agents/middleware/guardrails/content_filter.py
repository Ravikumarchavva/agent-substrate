from __future__ import annotations

import re
from typing import Callable, Awaitable, ClassVar

from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.exceptions import MiddlewareTermination
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.kernel.core.content import TextBlock


class ContentFilterMiddleware:
    """Block messages that match any pattern in a configurable blocklist."""

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TURN})

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
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        messages = context.messages or []
        if not messages:
            await call_next()
            return

        last_msg = messages[-1]
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
