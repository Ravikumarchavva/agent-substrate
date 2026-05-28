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

_INJECTION_PATTERNS: List[re.Pattern] = [
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
    re.compile(r"do\s+anything\s+now", re.I),  # DAN attack
    re.compile(r"developer\s+mode", re.I),
]


@register_guardrail("prompt_injection")
class PromptInjectionGuardrail(BaseGuardrail):
    """Detect common prompt injection / jailbreak attempts.

    Uses a curated set of regex patterns that catch the most common
    injection vectors.  For production, combine with an LLM judge.

    Args:
        extra_patterns: Additional regex strings to match.
        tripwire: Hard stop on detection.
    """

    name = "prompt_injection"
    description = "Detects common prompt injection and jailbreak patterns"
    guardrail_type = GuardrailType.INPUT

    def __init__(
        self,
        *,
        extra_patterns: Optional[List[str]] = None,
        tripwire: bool = True,
    ):
        self.tripwire = tripwire
        self._patterns = list(_INJECTION_PATTERNS)
        if extra_patterns:
            for p in extra_patterns:
                try:
                    self._patterns.append(re.compile(p, re.I))
                except re.error as e:
                    raise ValueError(f"Invalid extra_pattern regex '{p}': {e}") from e

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = ctx.input_text or ""
        if not text:
            return self._pass("No input to check")

        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                return self._fail(
                    f"Potential prompt injection detected: '{match.group()[:60]}'",
                    tripwire=self.tripwire,
                    matched_pattern=pattern.pattern,
                    matched_text=match.group()[:80],
                )

        return self._pass("No injection patterns detected")
