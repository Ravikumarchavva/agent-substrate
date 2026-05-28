from __future__ import annotations

import re
from typing import List, Optional

from ravi.kernel.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.kernel.plugin import register_guardrail


@register_guardrail("content_filter")
class ContentFilterGuardrail(BaseGuardrail):
    """Block messages that match any pattern in a configurable blocklist.

    Works for both input and output guardrail positions.

    Args:
        blocked_patterns: List of regex patterns to block.
        blocked_keywords: List of exact keywords to block (case-insensitive).
        tripwire: If True, matching content triggers a hard stop.
    """

    name = "content_filter"
    description = "Blocks messages matching configurable keyword / regex patterns"

    def __init__(
        self,
        *,
        guardrail_type: GuardrailType = GuardrailType.INPUT,
        blocked_patterns: Optional[List[str]] = None,
        blocked_keywords: Optional[List[str]] = None,
        tripwire: bool = True,
    ):
        self.guardrail_type = guardrail_type
        self.tripwire = tripwire
        self.blocked_keywords = [kw.lower() for kw in (blocked_keywords or [])]
        self._compiled = []
        for p in blocked_patterns or []:
            try:
                self._compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                raise ValueError(f"Invalid blocked_pattern regex '{p}': {e}") from e

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = (
            ctx.input_text
            if self.guardrail_type == GuardrailType.INPUT
            else ctx.output_text
        )
        if not text:
            return self._pass("No text to check")

        text_lower = text.lower()

        for kw in self.blocked_keywords:
            if kw in text_lower:
                return self._fail(
                    f"Blocked keyword detected: '{kw}'",
                    tripwire=self.tripwire,
                    matched_keyword=kw,
                )

        for pattern in self._compiled:
            match = pattern.search(text)
            if match:
                return self._fail(
                    f"Blocked pattern matched: '{pattern.pattern}'",
                    tripwire=self.tripwire,
                    matched_pattern=pattern.pattern,
                )

        return self._pass("Content check passed")
