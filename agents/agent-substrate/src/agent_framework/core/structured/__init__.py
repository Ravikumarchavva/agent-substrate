"""agent_framework.core.structured — Structured outputs and LLM judges.

Public API::

    from agent_framework.core.structured import (
        # Core result type
        StructuredOutputResult,
        StructuredOutputError,

        # Entry points
        parse,              # standalone coroutine — no agent required

        # Guardrail-based judge
        LLMJudge,

        # Deterministic multi-agent router
        StructuredRouter,

        # Pre-built judge schemas
        ContentSafetyJudge,
        RelevanceJudge,
        ClassificationResult,
        ExtractionResult,
    )
"""
from agent_framework.core.structured.result import StructuredOutputError, StructuredOutputResult
from agent_framework.core.structured.parse import parse
from agent_framework.core.structured.judge import LLMJudge
from agent_framework.core.structured.router import StructuredRouter
from agent_framework.core.structured.schemas import (
    ClassificationResult,
    ContentSafetyJudge,
    ExtractionResult,
    RelevanceJudge,
)

__all__ = [
    # Result
    "StructuredOutputResult",
    "StructuredOutputError",
    # Entry points
    "parse",
    # Guardrail
    "LLMJudge",
    # Router
    "StructuredRouter",
    # Schemas
    "ContentSafetyJudge",
    "RelevanceJudge",
    "ClassificationResult",
    "ExtractionResult",
]
