"""substrate — async AI-agent framework.

Quick-start for client apps::

    from substrate import ReActAgent, Runtime, create_model_client
    from substrate import ContentFilterMiddleware, MiddlewareTermination
    from substrate import TextDelta, CompletionEvent, StreamDone
"""

from __future__ import annotations

from substrate.version import __version__

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from substrate.integrations.llm.factory import LLMFactory, create_model_client
    from substrate.agents import ReActAgent
    from substrate.agents import OrchestratorAgent, SubAgentConfig
    from substrate.agents import UserProxyAgent
    from substrate.config import SubstrateConfig
    from substrate.stores import LocalFilesystemHistoryProvider
    from substrate.workspace import LocalFilesystemWorkspaceStore
    from substrate.stores import LocalFilesystemShortTermMemory
    from substrate.stores import WorkspaceFileStore
    from substrate.context import AgentContext, ContextConfig
    from substrate.context import SlidingWindowCompaction
    from substrate.middleware import AgentRunResult, MiddlewareContext
    from substrate.middleware import MiddlewareStage
    from substrate.middleware import AuditLoggerMiddleware
    from substrate.middleware import CacheMiddleware
    from substrate.middleware import ContentFilterMiddleware
    from substrate.middleware import ContentTruncatorMiddleware
    from substrate.middleware import FileValidatorMiddleware
    from substrate.middleware import HistoryTruncatorMiddleware
    from substrate.middleware import LLMJudgeMiddleware
    from substrate.middleware import MaxTokenMiddleware
    from substrate.middleware import PIIDetectionMiddleware
    from substrate.middleware import PromptInjectionMiddleware
    from substrate.middleware import RateLimiterMiddleware
    from substrate.middleware import RetryMiddleware
    from substrate.middleware import SchemaValidatorMiddleware
    from substrate.middleware import ToolCallValidationMiddleware
    from substrate.middleware import MiddlewarePipeline
    from substrate.runtime import Runtime, RunOutcome
    from substrate.tools import Skill
    from substrate.types import MiddlewareTermination
    from substrate.types import ChatMessage, TextBlock
    from substrate.tools import ToolExecutionResult
    from substrate.types import CompletionEvent, ReasoningDelta, StreamDone, TextDelta

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
    "ReActAgent": ("substrate.agents.react", "ReActAgent"),
    "OrchestratorAgent": ("substrate.agents.orchestrator", "OrchestratorAgent"),
    "SubAgentConfig": ("substrate.agents.orchestrator", "SubAgentConfig"),
    "UserProxyAgent": ("substrate.agents.proxy", "UserProxyAgent"),
    # runtime
    "Runtime": ("substrate.runtime", "Runtime"),
    "RunOutcome": ("substrate.runtime", "RunOutcome"),
    # config
    "SubstrateConfig": ("substrate.config", "SubstrateConfig"),
    # native durable storage
    "LocalFilesystemHistoryProvider": (
        "substrate.stores.local.threads",
        "LocalFilesystemHistoryProvider",
    ),
    "LocalHistoryProvider": (
        "substrate.stores.local.threads",
        "LocalFilesystemHistoryProvider",
    ),
    "LocalFilesystemWorkspaceStore": (
        "substrate.workspace.local_store",
        "LocalFilesystemWorkspaceStore",
    ),
    "LocalWorkspaceStore": (
        "substrate.workspace.local_store",
        "LocalFilesystemWorkspaceStore",
    ),
    "LocalFilesystemShortTermMemory": (
        "substrate.stores.local.short_term_memory",
        "LocalFilesystemShortTermMemory",
    ),
    "WorkspaceFileStore": (
        "substrate.stores.local.files",
        "WorkspaceFileStore",
    ),
    "LocalFileStore": (
        "substrate.stores.local.files",
        "WorkspaceFileStore",
    ),
    # supporting
    "AgentRunResult": ("substrate.middleware", "AgentRunResult"),
    "Skill": ("substrate.tools", "Skill"),
    "AgentContext": ("substrate.context", "AgentContext"),
    "ContextConfig": ("substrate.context", "ContextConfig"),
    "SlidingWindowCompaction": ("substrate.context", "SlidingWindowCompaction"),
    # middleware
    "MiddlewareContext": ("substrate.middleware", "MiddlewareContext"),
    "MiddlewareStage": ("substrate.middleware", "MiddlewareStage"),
    "MiddlewarePipeline": ("substrate.middleware", "MiddlewarePipeline"),
    "AuditLoggerMiddleware": ("substrate.middleware", "AuditLoggerMiddleware"),
    "CacheMiddleware": ("substrate.middleware", "CacheMiddleware"),
    "ContentFilterMiddleware": (
        "substrate.middleware",
        "ContentFilterMiddleware",
    ),
    "ContentTruncatorMiddleware": (
        "substrate.middleware",
        "ContentTruncatorMiddleware",
    ),
    "FileValidatorMiddleware": (
        "substrate.middleware",
        "FileValidatorMiddleware",
    ),
    "HistoryTruncatorMiddleware": (
        "substrate.middleware",
        "HistoryTruncatorMiddleware",
    ),
    "LLMJudgeMiddleware": ("substrate.middleware", "LLMJudgeMiddleware"),
    "MaxTokenMiddleware": ("substrate.middleware", "MaxTokenMiddleware"),
    "PIIDetectionMiddleware": ("substrate.middleware", "PIIDetectionMiddleware"),
    "PromptInjectionMiddleware": (
        "substrate.middleware",
        "PromptInjectionMiddleware",
    ),
    "RateLimiterMiddleware": ("substrate.middleware", "RateLimiterMiddleware"),
    "RetryMiddleware": ("substrate.middleware", "RetryMiddleware"),
    "SchemaValidatorMiddleware": (
        "substrate.middleware",
        "SchemaValidatorMiddleware",
    ),
    "ToolCallValidationMiddleware": (
        "substrate.middleware",
        "ToolCallValidationMiddleware",
    ),
    "MiddlewareTermination": ("substrate.types.errors", "MiddlewareTermination"),
    # factory
    "create_model_client": (
        "substrate.integrations.llm.factory",
        "create_model_client",
    ),
    "LLMFactory": ("substrate.integrations.llm.factory", "LLMFactory"),
    # stream
    "TextDelta": ("substrate.types.stream", "TextDelta"),
    "ReasoningDelta": ("substrate.types.stream", "ReasoningDelta"),
    "CompletionEvent": ("substrate.types.stream", "CompletionEvent"),
    "StreamDone": ("substrate.types.stream", "StreamDone"),
    # kernel types
    "ChatMessage": ("substrate.types.content", "ChatMessage"),
    "TextBlock": ("substrate.types.content", "TextBlock"),
    "ToolExecutionResult": ("substrate.tools", "ToolExecutionResult"),
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
