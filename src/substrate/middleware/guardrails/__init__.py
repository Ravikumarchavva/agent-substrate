"""Safety and policy guardrails — middleware that validates and may halt execution.

Each class is an ordinary ``Middleware`` that raises ``MiddlewareTermination``
when its policy fires. Add them to a ``ReActAgent``'s one ``middleware``
pipeline (``MiddlewarePipeline``) — each declares which stage(s) it applies
to via a ``stages`` class attribute, so a TURN-stage guardrail (e.g.
``ContentFilterMiddleware``, input/prompt checking) and a TOOL-stage one
(e.g. ``PIIDetectionMiddleware``) are wired identically, in the same list:

    MiddlewareStage.TURN — one inbox message (input/prompt checking)
    MiddlewareStage.CHAT — one model.generate() call (token limits, LLM judge)
    MiddlewareStage.TOOL — one tool.execute() call (PII, tool validation)
"""

from __future__ import annotations

from substrate.kernel.middleware.guardrails.content_filter import (
    ContentFilterMiddleware,
)
from substrate.kernel.middleware.guardrails.llm_judge import LLMJudgeMiddleware
from substrate.kernel.middleware.guardrails.max_token import MaxTokenMiddleware
from substrate.kernel.middleware.guardrails.multimodal_safety import (
    MultimodalSafetyMiddleware,
)
from substrate.kernel.middleware.guardrails.pii import PIIDetectionMiddleware
from substrate.kernel.middleware.guardrails.prompt_injection import (
    PromptInjectionMiddleware,
)
from substrate.kernel.middleware.guardrails.tool_call_validation import (
    ToolCallValidationMiddleware,
)

__all__ = [
    "ContentFilterMiddleware",
    "LLMJudgeMiddleware",
    "MaxTokenMiddleware",
    "MultimodalSafetyMiddleware",
    "PIIDetectionMiddleware",
    "PromptInjectionMiddleware",
    "ToolCallValidationMiddleware",
]
