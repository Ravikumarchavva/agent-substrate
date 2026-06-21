"""ravi — async AI-agent framework.

Quick-start for client apps::

    from ravi import ReActAgent, Runtime, create_model_client
    from ravi import ContentFilterMiddleware, MiddlewareTermination
    from ravi import TextDelta, CompletionEvent, StreamDone
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_substrate.integrations.llm.factory import LLMFactory, create_model_client
    from agent_substrate.agents.core.react import ReActAgent
    from agent_substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
    from agent_substrate.agents.core.proxy import UserProxyAgent
    from agent_substrate.agents.core.information_agent import InformationAgent
    from agent_substrate.agents.core.personal_feed_agent import PersonalFeedAgent
    from agent_substrate.agents.context import (
        AgentContext,
        ContextConfig,
        InMemoryHistoryProvider,
        SlidingWindowCompaction,
    )
    from agent_substrate.agents.middleware import (
        AgentRunResult,
        AgentCallContext,
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
    from agent_substrate.agents.runtime import Runtime
    from agent_substrate.kernel.tools.skills import Skill
    from agent_substrate.kernel.core.errors import MiddlewareTermination
    from agent_substrate.kernel import ChatMessage, TextBlock, ToolExecutionResult
    from agent_substrate.kernel.messaging.stream import (
        CompletionEvent,
        ReasoningDelta,
        StreamDone,
        TextDelta,
    )

__all__ = [
    # agent types
    "ReActAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
    "UserProxyAgent",
    "InformationAgent",
    "PersonalFeedAgent",
    # runtime
    "Runtime",
    # supporting types
    "AgentRunResult",
    "Skill",
    "InMemoryHistoryProvider",
    "AgentContext",
    "ContextConfig",
    "SlidingWindowCompaction",
    # middleware
    "AgentCallContext",
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
    # agent types
    "ReActAgent": ("agent_substrate.agents.core.react", "ReActAgent"),
    "OrchestratorAgent": ("agent_substrate.agents.core.orchestrator", "OrchestratorAgent"),
    "SubAgentConfig": ("agent_substrate.agents.core.orchestrator", "SubAgentConfig"),
    "UserProxyAgent": ("agent_substrate.agents.core.proxy", "UserProxyAgent"),
    "InformationAgent": ("agent_substrate.agents.core.information_agent", "InformationAgent"),
    "PersonalFeedAgent": ("agent_substrate.agents.core.personal_feed_agent", "PersonalFeedAgent"),
    # runtime
    "Runtime": ("agent_substrate.agents.runtime", "Runtime"),
    # supporting
    "AgentRunResult": ("agent_substrate.agents.middleware", "AgentRunResult"),
    "Skill": ("agent_substrate.agents.skills", "Skill"),
    "InMemoryHistoryProvider": ("agent_substrate.agents.context", "InMemoryHistoryProvider"),
    "AgentContext": ("agent_substrate.agents.context", "AgentContext"),
    "ContextConfig": ("agent_substrate.agents.context", "ContextConfig"),
    "SlidingWindowCompaction": ("agent_substrate.agents.context", "SlidingWindowCompaction"),
    # middleware
    "AgentCallContext": ("agent_substrate.agents.middleware", "AgentCallContext"),
    "ChatContext": ("agent_substrate.agents.middleware", "ChatContext"),
    "FunctionContext": ("agent_substrate.agents.middleware", "FunctionContext"),
    "MiddlewarePipeline": ("agent_substrate.agents.middleware", "MiddlewarePipeline"),
    "AuditLoggerMiddleware": ("agent_substrate.agents.middleware", "AuditLoggerMiddleware"),
    "CacheMiddleware": ("agent_substrate.agents.middleware", "CacheMiddleware"),
    "ContentFilterMiddleware": ("agent_substrate.agents.middleware", "ContentFilterMiddleware"),
    "ContentTruncatorMiddleware": (
        "agent_substrate.agents.middleware",
        "ContentTruncatorMiddleware",
    ),
    "FileValidatorMiddleware": ("agent_substrate.agents.middleware", "FileValidatorMiddleware"),
    "HistoryTruncatorMiddleware": (
        "agent_substrate.agents.middleware",
        "HistoryTruncatorMiddleware",
    ),
    "LLMJudgeMiddleware": ("agent_substrate.agents.middleware", "LLMJudgeMiddleware"),
    "MaxTokenMiddleware": ("agent_substrate.agents.middleware", "MaxTokenMiddleware"),
    "PIIDetectionMiddleware": ("agent_substrate.agents.middleware", "PIIDetectionMiddleware"),
    "PromptInjectionMiddleware": (
        "agent_substrate.agents.middleware",
        "PromptInjectionMiddleware",
    ),
    "RateLimiterMiddleware": ("agent_substrate.agents.middleware", "RateLimiterMiddleware"),
    "RetryMiddleware": ("agent_substrate.agents.middleware", "RetryMiddleware"),
    "SchemaValidatorMiddleware": (
        "agent_substrate.agents.middleware",
        "SchemaValidatorMiddleware",
    ),
    "ToolCallValidationMiddleware": (
        "agent_substrate.agents.middleware",
        "ToolCallValidationMiddleware",
    ),
    "MiddlewareTermination": ("agent_substrate.kernel.errors", "MiddlewareTermination"),
    # factory
    "create_model_client": ("agent_substrate.integrations.llm.factory", "create_model_client"),
    "LLMFactory": ("agent_substrate.integrations.llm.factory", "LLMFactory"),
    # stream
    "TextDelta": ("agent_substrate.kernel.stream", "TextDelta"),
    "ReasoningDelta": ("agent_substrate.kernel.stream", "ReasoningDelta"),
    "CompletionEvent": ("agent_substrate.kernel.stream", "CompletionEvent"),
    "StreamDone": ("agent_substrate.kernel.stream", "StreamDone"),
    # kernel
    "ChatMessage": ("agent_substrate.kernel.content", "ChatMessage"),
    "TextBlock": ("agent_substrate.kernel.content", "TextBlock"),
    "ToolExecutionResult": ("agent_substrate.kernel.tools", "ToolExecutionResult"),
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
    """Entry point — run ``uvicorn agent_substrate.serving.monolith.app:app --port 8001 --reload``."""
    print("ravi — run `uvicorn agent_substrate.serving.monolith.app:app --port 8001 --reload`")
