"""Pre-built guardrails ready for production use.

All guardrails follow the same contract:
  - Never raise (wrap errors in a failing GuardrailResult).
  - Are configurable via constructor parameters.
  - Ship with sensible defaults.

Included:
  ┌──────────────────────────────┬──────────┬──────────────────────────────────┐
  │ Guardrail                    │ Type     │ What it does                     │
  ├──────────────────────────────┼──────────┼──────────────────────────────────┤
  │ ContentFilterGuardrail       │ I / O    │ Keyword / regex blocklist        │
  │ PIIDetectionGuardrail        │ I / O    │ Detects emails, phones, SSNs,…   │
  │ PromptInjectionGuardrail     │ I        │ Catches common injection attacks │
  │ MaxTokenGuardrail            │ I        │ Rejects overly long input        │
  │ ToolCallValidationGuardrail  │ T        │ Allowlist / blocklist + schema   │
  │ LLMJudgeGuardrail            │ I / O    │ Uses a second LLM to judge       │
  └──────────────────────────────┴──────────┴──────────────────────────────────┘
  I = input, O = output, T = tool_call
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from agent_framework.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)


# ---------------------------------------------------------------------------
# Content filter — keyword / regex blocklist
# ---------------------------------------------------------------------------

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
        # Pre-compile regexes (validate user-supplied patterns)
        self._compiled = []
        for p in (blocked_patterns or []):
            try:
                self._compiled.append(re.compile(p, re.IGNORECASE))
            except re.error as e:
                raise ValueError(f"Invalid blocked_pattern regex '{p}': {e}") from e

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = ctx.input_text if self.guardrail_type == GuardrailType.INPUT else ctx.output_text
        if not text:
            return self._pass("No text to check")

        text_lower = text.lower()

        # Keyword check
        for kw in self.blocked_keywords:
            if kw in text_lower:
                return self._fail(
                    f"Blocked keyword detected: '{kw}'",
                    tripwire=self.tripwire,
                    matched_keyword=kw,
                )

        # Regex check
        for pattern in self._compiled:
            match = pattern.search(text)
            if match:
                return self._fail(
                    f"Blocked pattern matched: '{pattern.pattern}'",
                    tripwire=self.tripwire,
                    matched_pattern=pattern.pattern,
                )

        return self._pass("Content check passed")


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------

# Patterns deliberately kept simple — production systems should use a
# specialised PII library (e.g. presidio, scrubadub).
_PII_PATTERNS: Dict[str, re.Pattern] = {
    "email": re.compile(
        r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", re.IGNORECASE
    ),
    "phone_us": re.compile(
        r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
    ),
    "ssn": re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b"
    ),
    "credit_card": re.compile(
        r"\b\d(?:[ -]?\d){12,18}\b"
    ),
    "ip_address": re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
    ),
}


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

        # Build pattern set
        self._patterns: Dict[str, re.Pattern] = {}
        allowed = set(pii_types) if pii_types else set(_PII_PATTERNS.keys())
        for label in allowed:
            if label in _PII_PATTERNS:
                self._patterns[label] = _PII_PATTERNS[label]

        # Custom patterns
        if custom_patterns:
            for label, pat_str in custom_patterns.items():
                try:
                    self._patterns[label] = re.compile(pat_str, re.IGNORECASE)
                except re.error as e:
                    raise ValueError(f"Invalid custom PII pattern '{label}': {e}") from e

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = ctx.input_text if self.guardrail_type == GuardrailType.INPUT else ctx.output_text
        if not text:
            return self._pass("No text to check")

        detected: Dict[str, str] = {}
        for label, pattern in self._patterns.items():
            match = pattern.search(text)
            if match:
                # Aggressive masking — minimize information leakage
                raw = match.group()
                if label in ("ssn", "credit_card"):
                    # Show only last 4 characters
                    masked = "*" * max(0, len(raw) - 4) + raw[-4:] if len(raw) > 4 else "****"
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


# ---------------------------------------------------------------------------
# Prompt injection detection
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.I),
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


# ---------------------------------------------------------------------------
# Max token guardrail
# ---------------------------------------------------------------------------

class MaxTokenGuardrail(BaseGuardrail):
    """Reject input that exceeds a token limit (estimated by char count).

    Uses a configurable chars-per-token ratio for estimation.  For exact
    counts, use tiktoken directly or subclass and override.

    Args:
        max_tokens: Maximum allowed tokens.
        chars_per_token: Approximate characters per token (default: 4).
        tripwire: Hard stop when exceeded.
    """

    name = "max_token"
    description = "Rejects input exceeding configurable token limit"
    guardrail_type = GuardrailType.INPUT

    def __init__(
        self,
        *,
        max_tokens: int = 4096,
        chars_per_token: float = 4.0,
        tripwire: bool = False,
    ):
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self.tripwire = tripwire

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = ctx.input_text or ""
        estimated_tokens = int(len(text) / self.chars_per_token)

        if estimated_tokens > self.max_tokens:
            return self._fail(
                f"Input too long: ~{estimated_tokens} tokens (max {self.max_tokens})",
                tripwire=self.tripwire,
                estimated_tokens=estimated_tokens,
                max_tokens=self.max_tokens,
            )

        return self._pass(
            f"Token count OK: ~{estimated_tokens}/{self.max_tokens}",
            estimated_tokens=estimated_tokens,
        )


# ---------------------------------------------------------------------------
# Tool-call validation
# ---------------------------------------------------------------------------

class ToolCallValidationGuardrail(BaseGuardrail):
    """Validate tool calls against allow/block lists and argument schemas.

    Args:
        allowed_tools: If set, only these tools may be called.
        blocked_tools: These tools are always blocked.
        blocked_argument_patterns: Dict of {tool_name: {arg_name: [regex_patterns]}}
                                   to block specific argument values.
        tripwire: Hard stop on violation.
    """

    name = "tool_call_validation"
    description = "Validates tool calls against allow/block lists and argument patterns"
    guardrail_type = GuardrailType.TOOL_CALL

    def __init__(
        self,
        *,
        allowed_tools: Optional[Set[str]] = None,
        blocked_tools: Optional[Set[str]] = None,
        blocked_argument_patterns: Optional[Dict[str, Dict[str, List[str]]]] = None,
        tripwire: bool = True,
    ):
        self.allowed_tools = allowed_tools
        self.blocked_tools = blocked_tools or set()
        self.tripwire = tripwire

        # Pre-compile argument patterns
        self._arg_patterns: Dict[str, Dict[str, List[re.Pattern]]] = {}
        if blocked_argument_patterns:
            for tool, args_map in blocked_argument_patterns.items():
                self._arg_patterns[tool] = {}
                for arg_name, patterns in args_map.items():
                    compiled = []
                    for p in patterns:
                        try:
                            compiled.append(re.compile(p, re.I))
                        except re.error as e:
                            raise ValueError(
                                f"Invalid blocked_argument_pattern regex for "
                                f"{tool}.{arg_name} '{p}': {e}"
                            ) from e
                    self._arg_patterns[tool][arg_name] = compiled

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        tool_name = ctx.tool_name or ""
        tool_args = ctx.tool_arguments or {}

        # Blocklist check
        if tool_name in self.blocked_tools:
            return self._fail(
                f"Tool '{tool_name}' is blocked",
                tripwire=self.tripwire,
                tool_name=tool_name,
            )

        # Allowlist check
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return self._fail(
                f"Tool '{tool_name}' is not in the allowed list",
                tripwire=self.tripwire,
                tool_name=tool_name,
                allowed_tools=sorted(self.allowed_tools),
            )

        # Argument pattern check
        if tool_name in self._arg_patterns:
            for arg_name, patterns in self._arg_patterns[tool_name].items():
                arg_value = str(tool_args.get(arg_name, ""))
                for pattern in patterns:
                    match = pattern.search(arg_value)
                    if match:
                        return self._fail(
                            f"Blocked argument pattern in {tool_name}.{arg_name}: "
                            f"'{match.group()[:40]}'",
                            tripwire=self.tripwire,
                            tool_name=tool_name,
                            argument_name=arg_name,
                            matched_pattern=pattern.pattern,
                        )

        return self._pass(f"Tool call '{tool_name}' validated")


# ---------------------------------------------------------------------------
# LLM Judge — uses a second model call to evaluate safety / relevance
# ---------------------------------------------------------------------------

class LLMJudgeGuardrail(BaseGuardrail):
    """Use a secondary LLM to judge content safety or policy compliance.

    This guardrail calls a (typically cheaper/faster) model to evaluate
    whether the content is safe.  The judge prompt is fully configurable.

    Args:
        model_client: A BaseModelClient instance for the judge model.
        judge_prompt: System prompt for the judge.  Must instruct the model
                      to respond with JSON: {"safe": bool, "reason": str}.
        guardrail_type: INPUT or OUTPUT.
        tripwire: Hard stop when the judge says unsafe.
    """

    name = "llm_judge"
    description = "Uses a secondary LLM to judge content safety"

    _DEFAULT_JUDGE_PROMPT = (
        "You are a content safety classifier. Evaluate if the following content "
        "is safe, appropriate, and free of harmful intent.\n\n"
        "Respond ONLY with a JSON object: {\"safe\": true/false, \"reason\": \"brief explanation\"}\n\n"
        "Content to evaluate:\n"
    )

    def __init__(
        self,
        *,
        model_client: Any,
        judge_prompt: Optional[str] = None,
        guardrail_type: GuardrailType = GuardrailType.INPUT,
        tripwire: bool = True,
    ):
        self.guardrail_type = guardrail_type
        self.tripwire = tripwire
        self._model_client = model_client
        self._judge_prompt = judge_prompt or self._DEFAULT_JUDGE_PROMPT

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = ctx.input_text if self.guardrail_type == GuardrailType.INPUT else ctx.output_text
        if not text:
            return self._pass("No text to judge")

        try:
            from agent_framework.messages.client_messages import SystemMessage, UserMessage

            messages = [
                SystemMessage(content=self._judge_prompt),
                UserMessage(content=[text]),
            ]
            response = await self._model_client.generate(messages=messages)

            # Parse judge response
            import json
            response_text = ""
            if response.content:
                response_text = " ".join(
                    str(c) for c in response.content if isinstance(c, str)
                )

            # Try to extract JSON from response
            judgment = self._parse_judgment(response_text)

            if not judgment.get("safe", True):
                return self._fail(
                    f"LLM judge flagged as unsafe: {judgment.get('reason', 'no reason')}",
                    tripwire=self.tripwire,
                    judge_response=judgment,
                )

            return self._pass(
                f"LLM judge passed: {judgment.get('reason', 'content is safe')}",
                judge_response=judgment,
            )

        except Exception as e:
            # Guardrails should never raise — fail open on judge errors
            return self._pass(
                f"LLM judge error (failing open): {str(e)}",
                error=str(e),
            )

    @staticmethod
    def _parse_judgment(text: str) -> Dict[str, Any]:
        """Extract JSON from potentially markdown-wrapped LLM response."""
        import json

        # Try direct parse
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass

        # Try extracting from markdown code block
        import re
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # Try finding any JSON object
        json_match = re.search(r"\{[^{}]*\"safe\"[^{}]*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: look for keywords
        lower = text.lower()
        if "unsafe" in lower or "not safe" in lower or "\"safe\": false" in lower:
            return {"safe": False, "reason": text[:200]}
        return {"safe": True, "reason": text[:200]}
