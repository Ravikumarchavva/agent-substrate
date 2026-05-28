"""ravi.kernel.structured — Structured output result types and schemas.

The kernel exposes only the *result* and *schema* primitives. Concrete
parsers and judges live in ``ravi.reasoning.structured``.

Public API::

    from ravi.kernel.structured import (
        StructuredOutputResult,
        StructuredOutputError,
        ContentSafetyJudge,
        RelevanceJudge,
        ClassificationResult,
        ExtractionResult,
    )
"""

from ravi.kernel.structured.result import (
    StructuredOutputError,
    StructuredOutputResult,
)
from ravi.kernel.structured.schemas import (
    ClassificationResult,
    ContentSafetyJudge,
    ExtractionResult,
    RelevanceJudge,
)

__all__ = [
    "StructuredOutputResult",
    "StructuredOutputError",
    "ContentSafetyJudge",
    "RelevanceJudge",
    "ClassificationResult",
    "ExtractionResult",
]
