from __future__ import annotations

import re
from typing import Callable, Awaitable

from ravi.agents.middleware._contracts import FunctionContext
from ravi.exceptions import MiddlewareTermination

_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(
        r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE
    ),
    "phone_us": re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


class PIIDetectionMiddleware:
    """Detect personally identifiable information in function arguments."""

    def __init__(
        self,
        *,
        pii_types: list[str] | None = None,
        custom_patterns: dict[str, str] | None = None,
    ):
        self._patterns: dict[str, re.Pattern[str]] = {}
        allowed = set(pii_types) if pii_types else set(_PII_PATTERNS.keys())
        for label in allowed:
            if label in _PII_PATTERNS:
                self._patterns[label] = _PII_PATTERNS[label]
        if custom_patterns:
            for label, pat_str in custom_patterns.items():
                try:
                    self._patterns[label] = re.compile(pat_str, re.IGNORECASE)
                except re.error as e:
                    raise ValueError(
                        f"Invalid custom PII pattern '{label}': {e}"
                    ) from e

    async def process(
        self, context: FunctionContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        if not context.arguments:
            await call_next()
            return

        for key, val in context.arguments.items():
            if not isinstance(val, str):
                continue
            for label, pattern in self._patterns.items():
                match = pattern.search(val)
                if match:
                    raise MiddlewareTermination(
                        f"PIIDetection: PII detected ({label}) in argument '{key}'"
                    )

        await call_next()
