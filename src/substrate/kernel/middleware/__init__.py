"""Middleware pipeline, infrastructure middleware, and guardrails."""

from __future__ import annotations

from substrate.kernel.abstractions.agent.middleware import MiddlewareStage
from substrate.kernel.middleware._contracts import (
    AgentRunResult,
    Middleware,
    MiddlewareContext,
    ToolCallRecord,
)

# Infrastructure middleware
from substrate.kernel.middleware.audit_logger import AuditLoggerMiddleware
from substrate.kernel.middleware.cache import CacheMiddleware
from substrate.kernel.middleware.content_truncator import ContentTruncatorMiddleware
from substrate.kernel.middleware.file_validator import FileValidatorMiddleware

# Observability
# Guardrails (safety / policy enforcement)
from substrate.kernel.middleware.guardrails import (
    ContentFilterMiddleware,
    LLMJudgeMiddleware,
    MaxTokenMiddleware,
    PIIDetectionMiddleware,
    PromptInjectionMiddleware,
    ToolCallValidationMiddleware,
)
from substrate.kernel.middleware.history_truncator import HistoryTruncatorMiddleware
from substrate.kernel.middleware.pipeline import MiddlewarePipeline
from substrate.kernel.middleware.rate_limiter import RateLimiterMiddleware
from substrate.kernel.middleware.retry import RetryMiddleware
from substrate.kernel.middleware.schema_validator import SchemaValidatorMiddleware

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
    # guardrails
    "ContentFilterMiddleware",
    "LLMJudgeMiddleware",
    "MaxTokenMiddleware",
    "PIIDetectionMiddleware",
    "PromptInjectionMiddleware",
    "ToolCallValidationMiddleware",
]
