"""ravi.core.structured — Structured outputs and LLM judges.

Public API::

    from ravi.core.structured import (
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

from ravi.core.structured.result import (
    StructuredOutputError,
    StructuredOutputResult,
)
from ravi.core.structured.parse import parse
from ravi.core.structured.judge import LLMJudge
from ravi.core.structured.router import StructuredRouter
from ravi.core.structured.schemas import (
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
