"""Middleware pipeline, infrastructure middleware, guardrails, and observability."""

from __future__ import annotations

from agent_substrate.agents.middleware.pipeline import MiddlewarePipeline
from agent_substrate.agents.middleware._contracts import (
    AgentCallContext,
    AgentRunResult,
    ChatContext,
    FunctionContext,
    ToolCallRecord,
)

# Infrastructure middleware
from agent_substrate.agents.middleware.audit_logger import AuditLoggerMiddleware
from agent_substrate.agents.middleware.cache import CacheMiddleware
from agent_substrate.agents.middleware.content_truncator import ContentTruncatorMiddleware
from agent_substrate.agents.middleware.file_validator import FileValidatorMiddleware
from agent_substrate.agents.middleware.history_truncator import HistoryTruncatorMiddleware
from agent_substrate.agents.middleware.rate_limiter import RateLimiterMiddleware
from agent_substrate.agents.middleware.retry import RetryMiddleware
from agent_substrate.agents.middleware.schema_validator import SchemaValidatorMiddleware

# Observability
from agent_substrate.agents.middleware.observability import (
    AgentTracingMiddleware,
    ChatTracingMiddleware,
)

# Guardrails (safety / policy enforcement)
from agent_substrate.agents.middleware.guardrails import (
    ContentFilterMiddleware,
    LLMJudgeMiddleware,
    MaxTokenMiddleware,
    PIIDetectionMiddleware,
    PromptInjectionMiddleware,
    ToolCallValidationMiddleware,
)

__all__ = [
    # pipeline
    "MiddlewarePipeline",
    # context and result types
    "AgentCallContext",
    "AgentRunResult",
    "ChatContext",
    "FunctionContext",
    "ToolCallRecord",
    # infrastructure
    "AuditLoggerMiddleware",
    "CacheMiddleware",
    "ContentTruncatorMiddleware",
    "FileValidatorMiddleware",
    "HistoryTruncatorMiddleware",
    "RateLimiterMiddleware",
    "RetryMiddleware",
    "SchemaValidatorMiddleware",
    # observability
    "AgentTracingMiddleware",
    "ChatTracingMiddleware",
    # guardrails
    "ContentFilterMiddleware",
    "LLMJudgeMiddleware",
    "MaxTokenMiddleware",
    "PIIDetectionMiddleware",
    "PromptInjectionMiddleware",
    "ToolCallValidationMiddleware",
]
