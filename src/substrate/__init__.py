"""substrate — async AI-agent framework.

Quick-start for client apps::

    from substrate import ReActAgent, Runtime, ToolRisk, tool

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def shout(text: str) -> str:
        "Upper-case the text."
        return text.upper()

    agent = ReActAgent("bot", model=my_model, tools=[shout])
    async with Runtime.open("./.substrate") as runtime:      # durable: a folder is the floor
        print((await runtime.run(agent, "Say hi loudly")).output)

Everything is imported from where it lives (``substrate.stores``, ``substrate.tools``…); the names here are the ones
nearly every program wants. Importing ``substrate`` imports nothing heavy.
"""

from __future__ import annotations

import logging

from substrate.version import __version__

from typing import TYPE_CHECKING

# A library installs no handler of its own: the application decides where records go (``substrate.logger.setup_logging``).
logging.getLogger("substrate").addHandler(logging.NullHandler())

if TYPE_CHECKING:
    from substrate.integrations.llm.factory import LLMFactory, create_model_client
    from substrate.agents import ReActAgent
    from substrate.agents import OrchestratorAgent, SubAgentConfig
    from substrate.agents import UserProxyAgent
    from substrate.stores import Store, connect
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
    from substrate.tools import (
        ApprovalDecision,
        AutoApprove,
        DurableApproval,
        Tool,
        ToolRisk,
        tool,
    )
    from substrate.models import ChatModel
    from substrate.server import create_app
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
    # native durable storage
    "Store",
    "connect",
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
    # tools and human approval
    "tool",
    "Tool",
    "ToolRisk",
    "ApprovalDecision",
    "DurableApproval",
    "AutoApprove",
    # the contract a model provider implements
    "ChatModel",
    # serve an agent over HTTP (needs the ``serve`` extra)
    "create_app",
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
    # native durable storage
    "Store": ("substrate.stores", "Store"),
    "connect": ("substrate.stores", "connect"),
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
    # tools and human approval
    "tool": ("substrate.tools", "tool"),
    "Tool": ("substrate.tools", "Tool"),
    "ToolRisk": ("substrate.tools", "ToolRisk"),
    "ApprovalDecision": ("substrate.tools", "ApprovalDecision"),
    "DurableApproval": ("substrate.tools", "DurableApproval"),
    "AutoApprove": ("substrate.tools", "AutoApprove"),
    "ChatModel": ("substrate.models", "ChatModel"),
    "create_app": ("substrate.server", "create_app"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        obj = getattr(importlib.import_module(module_path), attr)
        globals()[name] = obj
        return obj
    raise AttributeError(f"module 'substrate' has no attribute {name!r}")
