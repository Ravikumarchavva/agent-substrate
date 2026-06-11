"""ravi — async AI-agent framework.

Quick-start for client apps::

    from ravi import ReActAgent, LocalRuntime, create_model_client
    from ravi import ContentFilterMiddleware, MiddlewareTermination
    from ravi import TextDelta, CompletionEvent, StreamDone
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.integrations.llm.factory import LLMFactory, create_model_client
    from ravi.agents.core.react import AgentRunResult, ReActAgent
    from ravi.agents.context import (
        AgentContext,
        ContextConfig,
        InMemoryHistoryProvider,
        SlidingWindowCompaction,
    )
    from ravi.agents.middleware import (
        AgentRunContext,
        ChatContext,
        FunctionContext,
        AuditLoggerMiddleware,
        CacheMiddleware,
        ContentFilterMiddleware,
        ContentTruncatorMiddleware,
        FileValidatorMiddleware,
        HistoryTruncatorMiddleware,
        LLMJudgeMiddleware,
        MaxTokenMiddleware,
        PIIDetectionMiddleware,
        PromptInjectionMiddleware,
        RateLimiterMiddleware,
        RetryMiddleware,
        SchemaValidatorMiddleware,
        ToolCallValidationMiddleware,
        MiddlewarePipeline,
    )
    from ravi.agents.runtime import LocalRuntime
    from ravi.kernel.skills import Skill
    from ravi.kernel.errors import MiddlewareTermination
    from ravi.kernel import ChatMessage, TextBlock, ToolExecutionResult
    from ravi.kernel.stream import (
        CompletionEvent,
        ReasoningDelta,
        StreamDone,
        TextDelta,
    )

__all__ = [
    # agents
    "ReActAgent",
    "AgentRunResult",
    "LocalRuntime",
    "Skill",
    "InMemoryHistoryProvider",
    "AgentContext",
    "ContextConfig",
    "SlidingWindowCompaction",
    # middleware
    "AgentRunContext",
    "ChatContext",
    "FunctionContext",
    "MiddlewarePipeline",
    "AuditLoggerMiddleware",
    "CacheMiddleware",
    "ContentFilterMiddleware",
    "ContentTruncatorMiddleware",
    "FileValidatorMiddleware",
    "HistoryTruncatorMiddleware",
    "LLMJudgeMiddleware",
    "MaxTokenMiddleware",
    "PIIDetectionMiddleware",
    "PromptInjectionMiddleware",
    "RateLimiterMiddleware",
    "RetryMiddleware",
    "SchemaValidatorMiddleware",
    "ToolCallValidationMiddleware",
    "MiddlewareTermination",
    # llm
    "create_model_client",
    "LLMFactory",
    # stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    # kernel types
    "ChatMessage",
    "TextBlock",
    "ToolExecutionResult",
]

_LAZY: dict[str, tuple[str, str]] = {
    "ReActAgent": ("ravi.agents.core.react", "ReActAgent"),
    "AgentRunResult": ("ravi.agents.core.react", "AgentRunResult"),
    "LocalRuntime": ("ravi.agents.runtime", "LocalRuntime"),
    "Skill": ("ravi.agents.skills", "Skill"),
    "InMemoryHistoryProvider": ("ravi.agents.context", "InMemoryHistoryProvider"),
    "AgentContext": ("ravi.agents.context", "AgentContext"),
    "ContextConfig": ("ravi.agents.context", "ContextConfig"),
    "SlidingWindowCompaction": ("ravi.agents.context", "SlidingWindowCompaction"),
    # middleware
    "AgentRunContext": ("ravi.agents.middleware", "AgentRunContext"),
    "ChatContext": ("ravi.agents.middleware", "ChatContext"),
    "FunctionContext": ("ravi.agents.middleware", "FunctionContext"),
    "MiddlewarePipeline": ("ravi.agents.middleware", "MiddlewarePipeline"),
    "AuditLoggerMiddleware": ("ravi.agents.middleware", "AuditLoggerMiddleware"),
    "CacheMiddleware": ("ravi.agents.middleware", "CacheMiddleware"),
    "ContentFilterMiddleware": ("ravi.agents.middleware", "ContentFilterMiddleware"),
    "ContentTruncatorMiddleware": (
        "ravi.agents.middleware",
        "ContentTruncatorMiddleware",
    ),
    "FileValidatorMiddleware": ("ravi.agents.middleware", "FileValidatorMiddleware"),
    "HistoryTruncatorMiddleware": (
        "ravi.agents.middleware",
        "HistoryTruncatorMiddleware",
    ),
    "LLMJudgeMiddleware": ("ravi.agents.middleware", "LLMJudgeMiddleware"),
    "MaxTokenMiddleware": ("ravi.agents.middleware", "MaxTokenMiddleware"),
    "PIIDetectionMiddleware": ("ravi.agents.middleware", "PIIDetectionMiddleware"),
    "PromptInjectionMiddleware": (
        "ravi.agents.middleware",
        "PromptInjectionMiddleware",
    ),
    "RateLimiterMiddleware": ("ravi.agents.middleware", "RateLimiterMiddleware"),
    "RetryMiddleware": ("ravi.agents.middleware", "RetryMiddleware"),
    "SchemaValidatorMiddleware": (
        "ravi.agents.middleware",
        "SchemaValidatorMiddleware",
    ),
    "ToolCallValidationMiddleware": (
        "ravi.agents.middleware",
        "ToolCallValidationMiddleware",
    ),
    "MiddlewareTermination": ("ravi.kernel.errors", "MiddlewareTermination"),
    # factory
    "create_model_client": ("ravi.integrations.llm.factory", "create_model_client"),
    "LLMFactory": ("ravi.integrations.llm.factory", "LLMFactory"),
    # stream
    "TextDelta": ("ravi.kernel.stream", "TextDelta"),
    "ReasoningDelta": ("ravi.kernel.stream", "ReasoningDelta"),
    "CompletionEvent": ("ravi.kernel.stream", "CompletionEvent"),
    "StreamDone": ("ravi.kernel.stream", "StreamDone"),
    # kernel
    "ChatMessage": ("ravi.kernel.content", "ChatMessage"),
    "TextBlock": ("ravi.kernel.content", "TextBlock"),
    "ToolExecutionResult": ("ravi.kernel.tools", "ToolExecutionResult"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        obj = getattr(importlib.import_module(module_path), attr)
        globals()[name] = obj
        return obj
    raise AttributeError(f"module 'ravi' has no attribute {name!r}")


def main() -> None:
    """Entry point — run ``uvicorn ravi.serving.monolith.app:app --port 8001 --reload``."""
    print("ravi — run `uvicorn ravi.serving.monolith.app:app --port 8001 --reload`")
