"""substrate — async AI-agent framework.

Quick-start for client apps::

    from substrate import ReActAgent, Runtime, create_model_client
    from substrate import ContentFilterMiddleware, MiddlewareTermination
    from substrate import TextDelta, CompletionEvent, StreamDone
"""

from __future__ import annotations

__version__ = "0.1.0"

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from substrate.integrations.llm.factory import LLMFactory, create_model_client
    from substrate.kernel.agents.react import ReActAgent
    from substrate.kernel.agents.orchestrator import OrchestratorAgent, SubAgentConfig
    from substrate.kernel.agents.proxy import UserProxyAgent
    from substrate.config import SubstrateConfig
    from substrate.kernel.storage.local_history import LocalFilesystemHistoryProvider
    from substrate.kernel.workspace.local_workspace_store import (
        LocalFilesystemWorkspaceStore,
    )
    from substrate.kernel.storage.local_short_term_memory import LocalFilesystemShortTermMemory
    from substrate.kernel.storage.local_object_store import WorkspaceFileStore
    from substrate.kernel.context import (
        AgentContext,
        ContextConfig,
        SlidingWindowCompaction,
    )
    from substrate.kernel.storage import (
        InMemoryHistoryProvider,
    )
    from substrate.kernel.middleware import (
        AgentRunResult,
        MiddlewareContext,
        MiddlewareStage,
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
    from substrate.kernel.runtime import Runtime, RunOutcome
    from substrate.kernel.abstractions.tools import Skill
    from substrate.kernel.abstractions.exceptions import MiddlewareTermination
    from substrate.kernel.abstractions import ChatMessage, TextBlock, ToolExecutionResult
    from substrate.kernel.abstractions.messaging.stream import (
        CompletionEvent,
        ReasoningDelta,
        StreamDone,
        TextDelta,
    )

__all__ = [
    # version
    "__version__",
    # agent types
    "ReActAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
    "UserProxyAgent",
    # runtime
    "Runtime",
    "RunOutcome",
    # config
    "SubstrateConfig",
    # native durable storage
    "LocalFilesystemHistoryProvider",
    "LocalHistoryProvider",
    "LocalFilesystemWorkspaceStore",
    "LocalWorkspaceStore",
    "LocalFilesystemShortTermMemory",
    "WorkspaceFileStore",
    "LocalFileStore",
    # supporting types
    "AgentRunResult",
    "Skill",
    "InMemoryHistoryProvider",
    "AgentContext",
    "ContextConfig",
    "SlidingWindowCompaction",
    # middleware
    "MiddlewareContext",
    "MiddlewareStage",
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
    "ReActAgent": ("substrate.kernel.agents.react", "ReActAgent"),
    "OrchestratorAgent": ("substrate.kernel.agents.orchestrator", "OrchestratorAgent"),
    "SubAgentConfig": ("substrate.kernel.agents.orchestrator", "SubAgentConfig"),
    "UserProxyAgent": ("substrate.kernel.agents.proxy", "UserProxyAgent"),
    # runtime
    "Runtime": ("substrate.kernel.runtime", "Runtime"),
    "RunOutcome": ("substrate.kernel.runtime", "RunOutcome"),
    # config
    "SubstrateConfig": ("substrate.config", "SubstrateConfig"),
    # native durable storage
    "LocalFilesystemHistoryProvider": (
        "substrate.kernel.storage.local_history",
        "LocalFilesystemHistoryProvider",
    ),
    "LocalHistoryProvider": (
        "substrate.kernel.storage.local_history",
        "LocalFilesystemHistoryProvider",
    ),
    "LocalFilesystemWorkspaceStore": (
        "substrate.kernel.workspace.local_workspace_store",
        "LocalFilesystemWorkspaceStore",
    ),
    "LocalWorkspaceStore": (
        "substrate.kernel.workspace.local_workspace_store",
        "LocalFilesystemWorkspaceStore",
    ),
    "LocalFilesystemShortTermMemory": (
        "substrate.kernel.storage.local_short_term_memory",
        "LocalFilesystemShortTermMemory",
    ),
    "WorkspaceFileStore": (
        "substrate.kernel.storage.local_object_store",
        "WorkspaceFileStore",
    ),
    "LocalFileStore": (
        "substrate.kernel.storage.local_object_store",
        "WorkspaceFileStore",
    ),
    # supporting
    "AgentRunResult": ("substrate.kernel.middleware", "AgentRunResult"),
    "Skill": ("substrate.kernel.abstractions.tools", "Skill"),
    "InMemoryHistoryProvider": (
        "substrate.kernel.storage.local_history",
        "LocalFilesystemHistoryProvider",
    ),
    "AgentContext": ("substrate.kernel.context", "AgentContext"),
    "ContextConfig": ("substrate.kernel.context", "ContextConfig"),
    "SlidingWindowCompaction": ("substrate.kernel.context", "SlidingWindowCompaction"),
    # middleware
    "MiddlewareContext": ("substrate.kernel.middleware", "MiddlewareContext"),
    "MiddlewareStage": ("substrate.kernel.middleware", "MiddlewareStage"),
    "MiddlewarePipeline": ("substrate.kernel.middleware", "MiddlewarePipeline"),
    "AuditLoggerMiddleware": ("substrate.kernel.middleware", "AuditLoggerMiddleware"),
    "CacheMiddleware": ("substrate.kernel.middleware", "CacheMiddleware"),
    "ContentFilterMiddleware": (
        "substrate.kernel.middleware",
        "ContentFilterMiddleware",
    ),
    "ContentTruncatorMiddleware": (
        "substrate.kernel.middleware",
        "ContentTruncatorMiddleware",
    ),
    "FileValidatorMiddleware": (
        "substrate.kernel.middleware",
        "FileValidatorMiddleware",
    ),
    "HistoryTruncatorMiddleware": (
        "substrate.kernel.middleware",
        "HistoryTruncatorMiddleware",
    ),
    "LLMJudgeMiddleware": ("substrate.kernel.middleware", "LLMJudgeMiddleware"),
    "MaxTokenMiddleware": ("substrate.kernel.middleware", "MaxTokenMiddleware"),
    "PIIDetectionMiddleware": ("substrate.kernel.middleware", "PIIDetectionMiddleware"),
    "PromptInjectionMiddleware": (
        "substrate.kernel.middleware",
        "PromptInjectionMiddleware",
    ),
    "RateLimiterMiddleware": ("substrate.kernel.middleware", "RateLimiterMiddleware"),
    "RetryMiddleware": ("substrate.kernel.middleware", "RetryMiddleware"),
    "SchemaValidatorMiddleware": (
        "substrate.kernel.middleware",
        "SchemaValidatorMiddleware",
    ),
    "ToolCallValidationMiddleware": (
        "substrate.kernel.middleware",
        "ToolCallValidationMiddleware",
    ),
    "MiddlewareTermination": ("substrate.kernel.abstractions.exceptions", "MiddlewareTermination"),
    # factory
    "create_model_client": (
        "substrate.integrations.llm.factory",
        "create_model_client",
    ),
    "LLMFactory": ("substrate.integrations.llm.factory", "LLMFactory"),
    # stream
    "TextDelta": ("substrate.kernel.abstractions.messaging.stream", "TextDelta"),
    "ReasoningDelta": ("substrate.kernel.abstractions.messaging.stream", "ReasoningDelta"),
    "CompletionEvent": ("substrate.kernel.abstractions.messaging.stream", "CompletionEvent"),
    "StreamDone": ("substrate.kernel.abstractions.messaging.stream", "StreamDone"),
    # kernel types
    "ChatMessage": ("substrate.kernel.abstractions.core.content", "ChatMessage"),
    "TextBlock": ("substrate.kernel.abstractions.core.content", "TextBlock"),
    "ToolExecutionResult": ("substrate.kernel.abstractions.tools", "ToolExecutionResult"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        obj = getattr(importlib.import_module(module_path), attr)
        globals()[name] = obj
        return obj
    raise AttributeError(f"module 'substrate' has no attribute {name!r}")


def main() -> None:
    """Entry point — run ``uvicorn substrate.serving.monolith.app:app --port 8001 --reload``."""
    print(
        "substrate — run `uvicorn substrate.serving.monolith.app:app --port 8001 --reload`"
    )
