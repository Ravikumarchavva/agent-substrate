from __future__ import annotations

import re
from typing import Dict, List, Optional

from ravi.kernel.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.kernel.plugin import register_guardrail

# Patterns kept simple — production systems should use presidio or scrubadub.
_PII_PATTERNS: Dict[str, re.Pattern] = {
    "email": re.compile(
        r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE
    ),
    "phone_us": re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


@register_guardrail("pii")
class PIIDetectionGuardrail(BaseGuardrail):
    """Detect personally identifiable information in text.

    Args:
        pii_types: Which PII types to check. Default: all.
                   Allowed: "email", "phone_us", "ssn", "credit_card", "ip_address"
        tripwire: Hard stop on detection.
        custom_patterns: Dict of {label: regex_string} for additional patterns.
    """

    name = "pii_detection"
    description = "Detects PII (emails, phones, SSNs, credit cards, IPs)"

    def __init__(
        self,
        *,
        guardrail_type: GuardrailType = GuardrailType.INPUT,
        pii_types: Optional[List[str]] = None,
        tripwire: bool = True,
        custom_patterns: Optional[Dict[str, str]] = None,
    ):
        self.guardrail_type = guardrail_type
        self.tripwire = tripwire

        self._patterns: Dict[str, re.Pattern] = {}
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

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = (
            ctx.input_text
            if self.guardrail_type == GuardrailType.INPUT
            else ctx.output_text
        )
        if not text:
            return self._pass("No text to check")

        detected: Dict[str, str] = {}
        for label, pattern in self._patterns.items():
            match = pattern.search(text)
            if match:
                raw = match.group()
                if label in ("ssn", "credit_card"):
                    masked = (
                        "*" * max(0, len(raw) - 4) + raw[-4:]
                        if len(raw) > 4
                        else "****"
                    )
                else:
                    masked = "****"
                detected[label] = masked

        if detected:
            return self._fail(
                f"PII detected: {', '.join(detected.keys())}",
                tripwire=self.tripwire,
                detected_types=list(detected.keys()),
                masked_values=detected,
            )

        return self._pass("No PII detected")
