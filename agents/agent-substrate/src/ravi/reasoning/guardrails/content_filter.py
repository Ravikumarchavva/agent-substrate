from __future__ import annotations

import re

from ravi.reasoning.guardrails._contracts import (
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
    _fail,
    _pass,
)


class ContentFilterGuardrail:
    """Block messages that match any pattern in a configurable blocklist."""

    name = "content_filter"
    description = "Blocks messages matching configurable keyword / regex patterns"

    def __init__(
        self,
        *,
        guardrail_type: GuardrailType = GuardrailType.INPUT,
        blocked_patterns: list[str] | None = None,
        blocked_keywords: list[str] | None = None,
        tripwire: bool = True,
    ):
        self.guardrail_type = guardrail_type
        self.tripwire = tripwire
        self.blocked_keywords = [kw.lower() for kw in (blocked_keywords or [])]
        self._compiled: list[re.Pattern[str]] = []
        for p in blocked_patterns or []:
            try:
                self._compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                raise ValueError(f"Invalid blocked_pattern regex '{p}': {e}") from e

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = (
            ctx.input_text if self.guardrail_type == GuardrailType.INPUT else ctx.output_text
        )
        if not text:
            return _pass(self.name, self.guardrail_type, "No text to check")

        text_lower = text.lower()
        for kw in self.blocked_keywords:
            if kw in text_lower:
                return _fail(
                    self.name,
                    self.guardrail_type,
                    f"Blocked keyword detected: '{kw}'",
                    tripwire=self.tripwire,
                    matched_keyword=kw,
                )
        for pattern in self._compiled:
            match = pattern.search(text)
            if match:
                return _fail(
                    self.name,
                    self.guardrail_type,
                    f"Blocked pattern matched: '{pattern.pattern}'",
                    tripwire=self.tripwire,
                    matched_pattern=pattern.pattern,
                )
        return _pass(self.name, self.guardrail_type, "Content check passed")
