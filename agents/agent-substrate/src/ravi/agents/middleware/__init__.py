"""Middleware and Interceptor Pipeline.

Provides the ability to wrap the execution lifecycle of agents with orthogonal
concerns like Guardrails (PII redaction, Prompt Injection), Audit Logging,
and Semantic Caching, without polluting agent business logic.
"""

from ravi.agents.middleware.pipeline import Interceptor, MiddlewarePipeline
from ravi.agents.middleware.guardrails import PIIRedactionGuardrail, PromptInjectionGuardrail
from ravi.agents.middleware.audit import AuditLoggerMiddleware as L1AuditLoggerMiddleware
from ravi.agents.middleware._contracts import MiddlewareContext, MiddlewareStage
from ravi.agents.middleware.audit_logger import AuditLoggerMiddleware
from ravi.agents.middleware.cache import CacheMiddleware
from ravi.agents.middleware.content_truncator import ContentTruncatorMiddleware
from ravi.agents.middleware.file_validator import FileValidatorMiddleware
from ravi.agents.middleware.reasoning_guardrails import GuardrailsMiddleware
from ravi.agents.middleware.history_truncator import HistoryTruncatorMiddleware
from ravi.agents.middleware.rate_limiter import RateLimiterMiddleware
from ravi.agents.middleware.retry import RetryMiddleware
from ravi.agents.middleware.schema_validator import SchemaValidatorMiddleware

__all__ = [
    "Interceptor",
    "MiddlewarePipeline",
    "PIIRedactionGuardrail",
    "PromptInjectionGuardrail",
    "L1AuditLoggerMiddleware",
    # Elevated reasoning middlewares:
    "MiddlewareContext",
    "MiddlewareStage",
    "AuditLoggerMiddleware",
    "CacheMiddleware",
    "ContentTruncatorMiddleware",
    "FileValidatorMiddleware",
    "GuardrailsMiddleware",
    "HistoryTruncatorMiddleware",
    "RateLimiterMiddleware",
    "RetryMiddleware",
    "SchemaValidatorMiddleware",
]
