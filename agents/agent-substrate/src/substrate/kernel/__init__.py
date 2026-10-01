"""substrate.kernel — runtime services layer.

Provides the infrastructure agents run on top of: context and history
management, message middleware, resource budgets, supervision, and the
concrete agent types (ReActAgent, OrchestratorAgent, etc.).
"""

from __future__ import annotations

from substrate.kernel.context import (
    AgentContext,
    CompactionStrategy,
    ContextConfig,
    SlidingWindowCompaction,
    SummarizationCompaction,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
)
from substrate.kernel.storage import (
    HistoryProvider,
    InMemoryHistoryProvider,
)
from substrate.kernel.llm import (
    EmbeddingClient,
    LLMClient,
    MODEL_REGISTRY,
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)
from substrate.kernel.middleware import (
    AuditLoggerMiddleware,
    Middleware,
    MiddlewareStage,
    MiddlewarePipeline,
    MiddlewareContext,
    RateLimiterMiddleware,
    RetryMiddleware,
    CacheMiddleware,
    ContentTruncatorMiddleware,
    FileValidatorMiddleware,
    SchemaValidatorMiddleware,
    HistoryTruncatorMiddleware,
    ContentFilterMiddleware,
    PromptInjectionMiddleware,
    MaxTokenMiddleware,
    LLMJudgeMiddleware,
    PIIDetectionMiddleware,
    ToolCallValidationMiddleware,
)
from substrate.kernel.agents import (
    ReActAgent,
    UserProxyAgent,
    OrchestratorAgent,
    SubAgentConfig,
)
from substrate.kernel.runtime import Runtime, RunContext, RunOutcome
from substrate.kernel.flows import SequentialFlow, ParallelFlow, ConditionalFlow
from substrate.kernel.evals import (
    EvalCase,
    EvalDataset,
    LLMJudge,
    EvalReport,
    EvalRunner,
)

__all__ = [
    # context
    "AgentContext",
    "CompactionStrategy",
    "ContextConfig",
    "HistoryProvider",
    "InMemoryHistoryProvider",
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
    # evals
    "EvalCase",
    "EvalDataset",
    "LLMJudge",
    "EvalReport",
    "EvalRunner",
]
