"""Middleware pipeline, infrastructure middleware, guardrails, and observability."""

from __future__ import annotations

from ravi.agents.middleware.pipeline import MiddlewarePipeline
from ravi.agents.middleware._contracts import (
    AgentRunContext,
    AgentRunResult,
    ChatContext,
    FunctionContext,
    ToolCallRecord,
)

# Infrastructure middleware
from ravi.agents.middleware.audit_logger import AuditLoggerMiddleware
from ravi.agents.middleware.cache import CacheMiddleware
from ravi.agents.middleware.content_truncator import ContentTruncatorMiddleware
from ravi.agents.middleware.file_validator import FileValidatorMiddleware
from ravi.agents.middleware.history_truncator import HistoryTruncatorMiddleware
from ravi.agents.middleware.rate_limiter import RateLimiterMiddleware
from ravi.agents.middleware.retry import RetryMiddleware
from ravi.agents.middleware.schema_validator import SchemaValidatorMiddleware

# Observability
from ravi.agents.middleware.observability import (
    AgentTracingMiddleware,
    ChatTracingMiddleware,
)

# Guardrails (safety / policy enforcement)
from ravi.agents.middleware.guardrails import (
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
    "AgentRunContext",
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
