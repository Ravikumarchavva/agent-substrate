"""Middleware pipeline, infrastructure middleware, guardrails, and observability."""

from __future__ import annotations

from substrate.agents.middleware.pipeline import MiddlewarePipeline
from substrate.agents.middleware._contracts import (
    AgentCallContext,
    AgentRunResult,
    ChatContext,
    FunctionContext,
    ToolCallRecord,
)

# Infrastructure middleware
from substrate.agents.middleware.audit_logger import AuditLoggerMiddleware
from substrate.agents.middleware.cache import CacheMiddleware
from substrate.agents.middleware.content_truncator import ContentTruncatorMiddleware
from substrate.agents.middleware.file_validator import FileValidatorMiddleware
from substrate.agents.middleware.history_truncator import HistoryTruncatorMiddleware
from substrate.agents.middleware.rate_limiter import RateLimiterMiddleware
from substrate.agents.middleware.retry import RetryMiddleware
from substrate.agents.middleware.schema_validator import SchemaValidatorMiddleware

# Observability
from substrate.agents.middleware.observability import (
    AgentTracingMiddleware,
    ChatTracingMiddleware,
)

# Guardrails (safety / policy enforcement)
from substrate.agents.middleware.guardrails import (
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
