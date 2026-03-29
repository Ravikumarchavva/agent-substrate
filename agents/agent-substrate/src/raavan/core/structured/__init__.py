"""raavan.core.structured — Structured outputs and LLM judges.

Public API::

    from raavan.core.structured import (
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

from raavan.core.structured.result import (
    StructuredOutputError,
    StructuredOutputResult,
)
from raavan.core.structured.parse import parse
from raavan.core.structured.judge import LLMJudge
from raavan.core.structured.router import StructuredRouter
from raavan.core.structured.schemas import (
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
