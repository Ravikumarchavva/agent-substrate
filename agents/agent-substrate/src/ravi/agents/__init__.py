"""ravi.agents — runtime services layer.

Provides the infrastructure agents run on top of: context and history
management, message middleware, resource budgets, supervision, and the
concrete agent types (AssistantAgent, OrchestratorAgent, etc.).
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
from ravi.agents.skills import Skill
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
    PIIDetectionGuardrail,
    PromptInjectionGuardrail,
)
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
    # guardrails
    "PIIDetectionGuardrail",
    "PromptInjectionGuardrail",
    # resources
    "BudgetExceededError",
    "ExecutionBudget",
    "agent_span",
    # supervision
    "RetryPolicy",
]
