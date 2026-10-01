"""Middleware pipeline, infrastructure middleware, guardrails, and observability."""

from __future__ import annotations

from substrate.kernel.middleware.pipeline import MiddlewarePipeline
from substrate.kernel.middleware._contracts import (
    AgentRunResult,
    Middleware,
    MiddlewareContext,
    ToolCallRecord,
)
from substrate.kernel.abstractions.agent.middleware import MiddlewareStage

# Infrastructure middleware
from substrate.kernel.middleware.audit_logger import AuditLoggerMiddleware
from substrate.kernel.middleware.cache import CacheMiddleware
from substrate.kernel.middleware.content_truncator import ContentTruncatorMiddleware
from substrate.kernel.middleware.file_validator import FileValidatorMiddleware
from substrate.kernel.middleware.history_truncator import HistoryTruncatorMiddleware
from substrate.kernel.middleware.rate_limiter import RateLimiterMiddleware
from substrate.kernel.middleware.retry import RetryMiddleware
from substrate.kernel.middleware.schema_validator import SchemaValidatorMiddleware

# Observability
from substrate.kernel.middleware.observability import (
    AgentTracingMiddleware,
    ChatTracingMiddleware,
    FunctionTracingMiddleware,
)

# Guardrails (safety / policy enforcement)
from substrate.kernel.middleware.guardrails import (
    ContentFilterMiddleware,
    LLMJudgeMiddleware,
    MaxTokenMiddleware,
    PIIDetectionMiddleware,
    PromptInjectionMiddleware,
    ToolCallValidationMiddleware,
)

__all__ = [
    # pipeline
    "Middleware",
    "MiddlewareStage",
    "MiddlewarePipeline",
    # context and result types
    "MiddlewareContext",
    "AgentRunResult",
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
    "FunctionTracingMiddleware",
    # guardrails
    "ContentFilterMiddleware",
    "LLMJudgeMiddleware",
    "MaxTokenMiddleware",
    "PIIDetectionMiddleware",
    "PromptInjectionMiddleware",
    "ToolCallValidationMiddleware",
]
