"""ravi.fabric — runtime services layer.

Provides the infrastructure agents run on top of: capability discovery
(catalog), context and history management, agent lifecycle (spawning,
continuation), message middleware, resource budgets / secrets / tracing,
and supervision trees.
"""

from __future__ import annotations

from ravi.fabric.catalog import CapabilityRegistry, Capability, Namespace
from ravi.fabric.context import (
    AgentContext,
    CompactionStrategy,
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.fabric.lifecycle import Continuation, Spawner
from ravi.fabric.llm import (
    EmbeddingClient,
    LLMClient,
    MODEL_REGISTRY,
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)
from ravi.fabric.middleware import (
    AuditLoggerMiddleware,
    Interceptor,
    MiddlewarePipeline,
    PIIRedactionGuardrail,
    PromptInjectionGuardrail,
)
from ravi.fabric.resources import (
    BudgetExceededError,
    ExecutionBudget,
    SecretVault,
    agent_span,
)
from ravi.fabric.supervision import FailurePolicy, RetryPolicy, Supervisor

__all__ = [
    # catalog
    "CapabilityRegistry",
    "Capability",
    "Namespace",
    # context
    "AgentContext",
    "CompactionStrategy",
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "SlidingWindowCompaction",
    # lifecycle
    "Continuation",
    "Spawner",
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
    "PIIRedactionGuardrail",
    "PromptInjectionGuardrail",
    # resources
    "BudgetExceededError",
    "ExecutionBudget",
    "SecretVault",
    "agent_span",
    # supervision
    "FailurePolicy",
    "RetryPolicy",
    "Supervisor",
]
