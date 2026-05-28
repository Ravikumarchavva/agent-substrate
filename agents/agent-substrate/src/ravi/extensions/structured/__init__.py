"""ravi.extensions.structured — Concrete structured-output parsers and judges.

Public API::

    from ravi.extensions.structured import (
        parse,              # standalone coroutine — no agent required
        LLMJudge,           # guardrail-based judge
        StructuredRouter,   # deterministic multi-agent router
    )
"""

from ravi.extensions.structured.parse import parse
from ravi.extensions.structured.judge import LLMJudge
from ravi.extensions.structured.router import StructuredRouter

__all__ = [
    "parse",
    "LLMJudge",
    "StructuredRouter",
]
