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
    from substrate.agents.core.react import ReActAgent
    from substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
    from substrate.agents.core.proxy import UserProxyAgent
    from substrate.config import SubstrateConfig
    from substrate.agents.storage.local_history import LocalFilesystemHistoryProvider
    from substrate.agents.workspace.local_workspace_store import (
        LocalFilesystemWorkspaceStore,
    )
    from substrate.capabilities.memory.local_session_store import LocalFileSessionStore
    from substrate.capabilities.storage.workspace import WorkspaceFileStore
    from substrate.agents.context import (
        AgentContext,
        ContextConfig,
        SlidingWindowCompaction,
    )
    from substrate.agents.storage import (
        InMemoryHistoryProvider,
    )
    from substrate.agents.middleware import (
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
    from substrate.agents.runtime import Runtime, RunOutcome
    from substrate.kernel.tools import Skill
    from substrate.kernel.exceptions import MiddlewareTermination
    from substrate.kernel import ChatMessage, TextBlock, ToolExecutionResult
    from substrate.kernel.messaging.stream import (
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
    "LocalFileSessionStore",
    "LocalSessionStore",
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
    "ReActAgent": ("substrate.agents.core.react", "ReActAgent"),
    "OrchestratorAgent": ("substrate.agents.core.orchestrator", "OrchestratorAgent"),
    "SubAgentConfig": ("substrate.agents.core.orchestrator", "SubAgentConfig"),
    "UserProxyAgent": ("substrate.agents.core.proxy", "UserProxyAgent"),
    # runtime
    "Runtime": ("substrate.agents.runtime", "Runtime"),
    "RunOutcome": ("substrate.agents.runtime", "RunOutcome"),
    # config
    "SubstrateConfig": ("substrate.config", "SubstrateConfig"),
    # native durable storage
    "LocalFilesystemHistoryProvider": (
        "substrate.agents.storage.local_history",
        "LocalFilesystemHistoryProvider",
    ),
    "LocalHistoryProvider": (
        "substrate.agents.storage.local_history",
        "LocalFilesystemHistoryProvider",
    ),
    "LocalFilesystemWorkspaceStore": (
        "substrate.agents.workspace.local_workspace_store",
        "LocalFilesystemWorkspaceStore",
    ),
    "LocalWorkspaceStore": (
        "substrate.agents.workspace.local_workspace_store",
        "LocalFilesystemWorkspaceStore",
    ),
    "LocalFileSessionStore": (
        "substrate.capabilities.memory.local_session_store",
        "LocalFileSessionStore",
    ),
    "LocalSessionStore": (
        "substrate.capabilities.memory.local_session_store",
        "LocalFileSessionStore",
    ),
    "WorkspaceFileStore": (
        "substrate.capabilities.storage.workspace",
        "WorkspaceFileStore",
    ),
    "LocalFileStore": (
        "substrate.capabilities.storage.workspace",
        "WorkspaceFileStore",
    ),
    # supporting
    "AgentRunResult": ("substrate.agents.middleware", "AgentRunResult"),
    "Skill": ("substrate.kernel.tools", "Skill"),
    "InMemoryHistoryProvider": (
        "substrate.agents.storage.local_history",
        "LocalFilesystemHistoryProvider",
    ),
    "AgentContext": ("substrate.agents.context", "AgentContext"),
    "ContextConfig": ("substrate.agents.context", "ContextConfig"),
    "SlidingWindowCompaction": ("substrate.agents.context", "SlidingWindowCompaction"),
    # middleware
    "MiddlewareContext": ("substrate.agents.middleware", "MiddlewareContext"),
    "MiddlewareStage": ("substrate.agents.middleware", "MiddlewareStage"),
    "MiddlewarePipeline": ("substrate.agents.middleware", "MiddlewarePipeline"),
    "AuditLoggerMiddleware": ("substrate.agents.middleware", "AuditLoggerMiddleware"),
    "CacheMiddleware": ("substrate.agents.middleware", "CacheMiddleware"),
    "ContentFilterMiddleware": (
        "substrate.agents.middleware",
        "ContentFilterMiddleware",
    ),
    "ContentTruncatorMiddleware": (
        "substrate.agents.middleware",
        "ContentTruncatorMiddleware",
    ),
    "FileValidatorMiddleware": (
        "substrate.agents.middleware",
        "FileValidatorMiddleware",
    ),
    "HistoryTruncatorMiddleware": (
        "substrate.agents.middleware",
        "HistoryTruncatorMiddleware",
    ),
    "LLMJudgeMiddleware": ("substrate.agents.middleware", "LLMJudgeMiddleware"),
    "MaxTokenMiddleware": ("substrate.agents.middleware", "MaxTokenMiddleware"),
    "PIIDetectionMiddleware": ("substrate.agents.middleware", "PIIDetectionMiddleware"),
    "PromptInjectionMiddleware": (
        "substrate.agents.middleware",
        "PromptInjectionMiddleware",
    ),
    "RateLimiterMiddleware": ("substrate.agents.middleware", "RateLimiterMiddleware"),
    "RetryMiddleware": ("substrate.agents.middleware", "RetryMiddleware"),
    "SchemaValidatorMiddleware": (
        "substrate.agents.middleware",
        "SchemaValidatorMiddleware",
    ),
    "ToolCallValidationMiddleware": (
        "substrate.agents.middleware",
        "ToolCallValidationMiddleware",
    ),
    "MiddlewareTermination": ("substrate.kernel.exceptions", "MiddlewareTermination"),
    # factory
    "create_model_client": (
        "substrate.integrations.llm.factory",
        "create_model_client",
    ),
    "LLMFactory": ("substrate.integrations.llm.factory", "LLMFactory"),
    # stream
    "TextDelta": ("substrate.kernel.messaging.stream", "TextDelta"),
    "ReasoningDelta": ("substrate.kernel.messaging.stream", "ReasoningDelta"),
    "CompletionEvent": ("substrate.kernel.messaging.stream", "CompletionEvent"),
    "StreamDone": ("substrate.kernel.messaging.stream", "StreamDone"),
    # kernel types
    "ChatMessage": ("substrate.kernel.core.content", "ChatMessage"),
    "TextBlock": ("substrate.kernel.core.content", "TextBlock"),
    "ToolExecutionResult": ("substrate.kernel.tools", "ToolExecutionResult"),
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
