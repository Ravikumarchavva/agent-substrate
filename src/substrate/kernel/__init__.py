"""substrate.kernel — runtime services layer.

Provides the infrastructure agents run on top of: context and history
management, message middleware, resource budgets, supervision, and the
concrete agent types (ReActAgent, OrchestratorAgent, etc.).
"""

from __future__ import annotations

from substrate.kernel.agents import (
    OrchestratorAgent,
    ReActAgent,
    SubAgentConfig,
    UserProxyAgent,
)
from substrate.kernel.context import (
    AgentContext,
    CompactionStrategy,
    ContextConfig,
    SelectiveToolCallCompactionStrategy,
    SlidingWindowCompaction,
    SummarizationCompaction,
    TokenBudgetComposedStrategy,
    ToolResultCompactionStrategy,
    TruncationStrategy,
)
from substrate.kernel.flows import ConditionalFlow, ParallelFlow, SequentialFlow
from substrate.kernel.llm import (
    MODEL_REGISTRY,
    EmbeddingClient,
    LLMClient,
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)
from substrate.kernel.middleware import (
    AuditLoggerMiddleware,
    CacheMiddleware,
    ContentFilterMiddleware,
    ContentTruncatorMiddleware,
    FileValidatorMiddleware,
    HistoryTruncatorMiddleware,
    LLMJudgeMiddleware,
    MaxTokenMiddleware,
    Middleware,
    MiddlewareContext,
    MiddlewarePipeline,
    MiddlewareStage,
    PIIDetectionMiddleware,
    PromptInjectionMiddleware,
    RateLimiterMiddleware,
    RetryMiddleware,
    SchemaValidatorMiddleware,
    ToolCallValidationMiddleware,
)
from substrate.kernel.runtime import RunContext, RunOutcome, Runtime
from substrate.kernel.storage import (
    HistoryProvider,
)

__all__ = [
    # context
    "AgentContext",
    "CompactionStrategy",
    "ContextConfig",
    "HistoryProvider",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
    # llm
    "EmbeddingClient",
    "LLMClient",
    "MODEL_REGISTRY",
    "ModelProfile",
    "estimate_cost",
    "get_model_profile",
    "list_models",
    # middleware
    "Middleware",
    "MiddlewareStage",
    "MiddlewareContext",
    "RateLimiterMiddleware",
    "RetryMiddleware",
    "CacheMiddleware",
    "ContentTruncatorMiddleware",
    "FileValidatorMiddleware",
    "SchemaValidatorMiddleware",
    "HistoryTruncatorMiddleware",
    # guardrails
    "ContentFilterMiddleware",
    "PromptInjectionMiddleware",
    "MaxTokenMiddleware",
    "LLMJudgeMiddleware",
    "PIIDetectionMiddleware",
    "ToolCallValidationMiddleware",
    "AuditLoggerMiddleware",
    "MiddlewarePipeline",
    # agent types
    "ReActAgent",
    "UserProxyAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
    # runtime
    "Runtime",
    "RunOutcome",
    "RunContext",
    # flows
    "SequentialFlow",
    "ParallelFlow",
    "ConditionalFlow",
]
