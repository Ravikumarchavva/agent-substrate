"""agent_substrate.agents — runtime services layer.

Provides the infrastructure agents run on top of: context and history
management, message middleware, resource budgets, supervision, and the
concrete agent types (ReActAgent, OrchestratorAgent, etc.).
"""

from __future__ import annotations

from agent_substrate.agents.context import (
    AgentContext,
    CompactionStrategy,
    ContextConfig,
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    SummarizationCompaction,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
)
from agent_substrate.agents.llm import (
    EmbeddingClient,
    LLMClient,
    MODEL_REGISTRY,
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)
from agent_substrate.agents.middleware import (
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
from agent_substrate.agents.core import (
    ReActAgent,
    UserProxyAgent,
    OrchestratorAgent,
    SubAgentConfig,
    InformationAgent,
    PersonalFeedAgent,
)
from agent_substrate.agents.runtime import Runtime, RunContext, Worker
from agent_substrate.agents.resources import (
    ExecutionTracker,
)
from agent_substrate.agents.supervision import RetryPolicy

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
    "ExecutionTracker",
    # supervision
    "RetryPolicy",
]
