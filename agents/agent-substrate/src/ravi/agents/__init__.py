"""ravi.agents — runtime services layer.

Provides the infrastructure agents run on top of: context and history
management, message middleware, resource budgets, supervision, and the
concrete agent types (ReActAgent, OrchestratorAgent, etc.).
"""

from __future__ import annotations

from ravi.agents.context import (
    AgentContext,
    CompactionStrategy,
    DefaultAgentContext,
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.kernel.skills import Skill
from ravi.agents.llm import (
    EmbeddingClient,
    LLMClient,
    MODEL_REGISTRY,
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)
from ravi.agents.middleware import (
    AuditLoggerMiddleware,
    Interceptor,
    MiddlewarePipeline,
)
from ravi.agents.guardrails import (
    ContentFilterGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
    LLMJudgeGuardrail,
    MaxTokenGuardrail,
    PIIDetectionGuardrail,
    PromptInjectionGuardrail,
    ToolCallValidationGuardrail,
    run_guardrails,
)
from ravi.agents.core.react import ReActAgent, AgentRunResult
from ravi.agents.core.proxy import UserProxyAgent
from ravi.agents.runtime import LocalRuntime
from ravi.agents.resources import (
    BudgetExceededError,
    ExecutionBudget,
    agent_span,
)
from ravi.agents.supervision import RetryPolicy

__all__ = [
    # context
    "AgentContext",
    "CompactionStrategy",
    "DefaultAgentContext",
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "SlidingWindowCompaction",
    # skills
    "Skill",
    # llm
    "EmbeddingClient",
    "LLMClient",
    "MODEL_REGISTRY",
    "ModelProfile",
    "estimate_cost",
    "get_model_profile",
    "list_models",
    # middleware
    "AuditLoggerMiddleware",
    "Interceptor",
    "MiddlewarePipeline",
    # agents
    "ReActAgent",
    "AgentRunResult",
    "UserProxyAgent",
    # runtime
    "LocalRuntime",
    # guardrails
    "ContentFilterGuardrail",
    "GuardrailContext",
    "GuardrailResult",
    "GuardrailType",
    "LLMJudgeGuardrail",
    "MaxTokenGuardrail",
    "PIIDetectionGuardrail",
    "PromptInjectionGuardrail",
    "ToolCallValidationGuardrail",
    "run_guardrails",
    # resources
    "BudgetExceededError",
    "ExecutionBudget",
    "agent_span",
    # supervision
    "RetryPolicy",
]
