from __future__ import annotations

import re
from typing import Callable, Awaitable

from ravi.agents.middleware._contracts import AgentCallContext
from ravi.exceptions import MiddlewareTermination
from ravi.kernel.core.content import TextBlock

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
        re.I,
    ),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above|everything)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|the)\s+", re.I),
    re.compile(r"pretend\s+(?:you(?:'re|\s+are)\s+|to\s+be\s+)", re.I),
    re.compile(r"act\s+as\s+(?:a|an|if)\s+", re.I),
    re.compile(r"new\s+(?:system\s+)?instructions?:", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"\[system\]", re.I),
    re.compile(r"override\s+(?:your\s+)?(?:instructions?|rules?|guidelines?)", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"do\s+anything\s+now", re.I),
    re.compile(r"developer\s+mode", re.I),
]


class PromptInjectionMiddleware:
    """Detect common prompt injection / jailbreak attempts."""

    def __init__(
        self,
        *,
        extra_patterns: list[str] | None = None,
    ):
        self._patterns = list(_INJECTION_PATTERNS)
        if extra_patterns:
            for p in extra_patterns:
                try:
                    self._patterns.append(re.compile(p, re.I))
                except re.error as e:
                    raise ValueError(f"Invalid extra_pattern regex '{p}': {e}") from e

    async def process(
        self, context: AgentCallContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        if not context.messages:
            await call_next()
            return

        last_msg = context.messages[-1]
        text = " ".join(b.text for b in last_msg.content if isinstance(b, TextBlock))

        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                raise MiddlewareTermination(
                    f"PromptInjection: Potential injection detected: '{match.group()[:60]}'"
                )

        await call_next()
