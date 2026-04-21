"""Guardrails module — safety rails for agent execution.

Usage::

    from ravi.core.guardrails import (
        # Base
        BaseGuardrail, GuardrailContext, GuardrailResult, GuardrailType,
        # Runner
        run_guardrails,
        # Pre-built
        ContentFilterGuardrail,
        PIIDetectionGuardrail,
        PromptInjectionGuardrail,
        MaxTokenGuardrail,
        ToolCallValidationGuardrail,
        LLMJudgeGuardrail,
    )
"""

from ravi.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.core.guardrails.runner import run_guardrails
from ravi.core.guardrails.prebuilt import (
    ContentFilterGuardrail,
    PIIDetectionGuardrail,
    PromptInjectionGuardrail,
    MaxTokenGuardrail,
    ToolCallValidationGuardrail,
    LLMJudgeGuardrail,
)

__all__ = [
    # Base
    "BaseGuardrail",
    "GuardrailContext",
    "GuardrailResult",
    "GuardrailType",
    # Runner
    "run_guardrails",
    # Pre-built
    "ContentFilterGuardrail",
    "PIIDetectionGuardrail",
    "PromptInjectionGuardrail",
    "MaxTokenGuardrail",
    "ToolCallValidationGuardrail",
    "LLMJudgeGuardrail",
]
