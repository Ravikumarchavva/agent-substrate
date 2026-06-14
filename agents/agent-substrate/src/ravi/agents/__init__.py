"""ravi.agents — runtime services layer.

Provides the infrastructure agents run on top of: context and history
management, message middleware, resource budgets, supervision, and the
concrete agent types (ReActAgent, OrchestratorAgent, etc.).
"""

from __future__ import annotations

from ravi.agents.context import (
    AgentContext,
    CompactionStrategy,
    ContextConfig,
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowStrategy,
    SummarizationStrategy,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
    SlidingWindowCompaction,
    SummarizationCompaction,
)
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
    MiddlewarePipeline,
    AgentCallContext,
    ChatContext,
    FunctionContext,
    RateLimiterMiddleware,
    RetryMiddleware,
    CacheMiddleware,
    ContentTruncatorMiddleware,
    FileValidatorMiddleware,
    SchemaValidatorMiddleware,
    HistoryTruncatorMiddleware,
    AgentTracingMiddleware,
    ChatTracingMiddleware,
    ContentFilterMiddleware,
    PromptInjectionMiddleware,
    MaxTokenMiddleware,
    LLMJudgeMiddleware,
    PIIDetectionMiddleware,
    ToolCallValidationMiddleware,
)
from ravi.agents.core import (
    ReActAgent,
    UserProxyAgent,
    OrchestratorAgent,
    SubAgentConfig,
    InformationAgent,
    PersonalFeedAgent,
)
from ravi.agents.runtime import Runtime, RunContext, Worker
from ravi.agents.resources import (
    BudgetExceededError,
    ExecutionTracker,
)
from ravi.agents.supervision import RetryPolicy

__all__ = [
    # context
    "AgentContext",
    "CompactionStrategy",
    "ContextConfig",
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "SlidingWindowStrategy",
    "SummarizationStrategy",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    # llm
    "EmbeddingClient",
    "LLMClient",
    "MODEL_REGISTRY",
    "ModelProfile",
    "estimate_cost",
    "get_model_profile",
    "list_models",
    # middleware
    "AgentCallContext",
    "ChatContext",
    "FunctionContext",
    "RateLimiterMiddleware",
    "RetryMiddleware",
    "CacheMiddleware",
    "ContentTruncatorMiddleware",
    "FileValidatorMiddleware",
    "SchemaValidatorMiddleware",
    "HistoryTruncatorMiddleware",
    # observability
    "AgentTracingMiddleware",
    "ChatTracingMiddleware",
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
    "InformationAgent",
    "PersonalFeedAgent",
    # runtime
    "Runtime",
    "RunContext",
    "Worker",
    # resources
    "BudgetExceededError",
    "ExecutionTracker",
    # supervision
    "RetryPolicy",
]
