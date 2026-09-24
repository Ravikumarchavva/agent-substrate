from __future__ import annotations

import re
from typing import Awaitable, Callable, ClassVar, Iterator

from substrate.agents.middleware._contracts import MiddlewareContext
from substrate.exceptions import MiddlewareTermination
from substrate.kernel.agent.middleware import MiddlewareStage

_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(
        r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE
    ),
    "phone_us": re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


def _strings(value: object) -> Iterator[str]:
    """Every string inside *value*, however deeply nested — a tool argument can
    be a dict or list, and PII in ``{"body": {"email": ...}}`` must not slip by."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


class PIIDetectionMiddleware:
    """Detect personally identifiable information in function arguments."""

    stages: ClassVar[frozenset[MiddlewareStage]] = frozenset({MiddlewareStage.TOOL})

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
        self, context: MiddlewareContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        arguments = context.arguments or {}
        if not arguments:
            await call_next()
            return

        for key, val in arguments.items():
            for text in _strings(val):
                for label, pattern in self._patterns.items():
                    if pattern.search(text):
                        raise MiddlewareTermination(
                            f"PIIDetection: PII detected ({label}) in argument '{key}'"
                        )

        await call_next()
