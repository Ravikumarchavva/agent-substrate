"""Safety and policy guardrails — middleware that validates and may halt execution.

Each class is a typed middleware (AgentMiddleware, ChatMiddleware, or
FunctionMiddleware) that raises ``MiddlewareTermination`` when its policy fires.
Register them on ``ReActAgent`` at the appropriate level:

    agent_middleware  — runs once per agent.run() call (input/prompt checking)
    chat_middleware   — runs around every model.generate() (token limits, LLM judge)
    function_middleware — runs around every tool.execute() (PII, tool validation)
"""

from __future__ import annotations

from substrate.agents.middleware.guardrails.content_filter import (
    ContentFilterMiddleware,
)
from substrate.agents.middleware.guardrails.llm_judge import LLMJudgeMiddleware
from substrate.agents.middleware.guardrails.max_token import MaxTokenMiddleware
from substrate.agents.middleware.guardrails.pii import PIIDetectionMiddleware
from substrate.agents.middleware.guardrails.prompt_injection import (
    PromptInjectionMiddleware,
)
from substrate.agents.middleware.guardrails.tool_call_validation import (
    ToolCallValidationMiddleware,
)

__all__ = [
    "ContentFilterMiddleware",
    "LLMJudgeMiddleware",
    "MaxTokenMiddleware",
    "PIIDetectionMiddleware",
    "PromptInjectionMiddleware",
    "ToolCallValidationMiddleware",
]
