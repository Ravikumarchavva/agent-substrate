"""Loamz Agents: durable agents, tools and an agent server, built on Loamz Data.

    from loamz.agents import ReActAgent, Runtime, ToolRisk, connect, create_model_client, tool

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def shout(text: str) -> str:
        "Upper-case the text."
        return text.upper()

    store = await connect("./.loamz/agents.db")            # SQLite; AgentStore(postgres(dsn)) for many machines
    agent = ReActAgent("bot", model=create_model_client("gpt-4o-mini", api_keys=keys), tools=[shout])
    async with Runtime(store, owns_store=True) as runtime:
        print((await runtime.run(agent, "Say hi loudly")).output)

The names here are the ones nearly every program wants; everything else is imported from where it lives
(``loamz.agents.kernel.*``, ``loamz.agents.stores``, ``loamz.agents.providers``, ``loamz.agents.toolkit``).
Importing ``loamz.agents`` imports nothing heavy: each name loads on first use.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

# A library installs no handler of its own: the application decides where records go (``bootstrap.logging``).
logging.getLogger("loamz.agents").addHandler(logging.NullHandler())

_LAZY: dict[str, str] = {
    # agents
    "ReActAgent": "loamz.agents.kernel.agents",
    "OrchestratorAgent": "loamz.agents.kernel.agents",
    "SubAgentConfig": "loamz.agents.kernel.agents",
    "UserProxyAgent": "loamz.agents.kernel.agents",
    "ChannelMemberAgent": "loamz.agents.kernel.agents",
    "SequentialFlow": "loamz.agents.kernel.agents",
    "ParallelFlow": "loamz.agents.kernel.agents",
    "ConditionalFlow": "loamz.agents.kernel.agents",
    # running them
    "Runtime": "loamz.agents.kernel.runtime",
    "RunOutcome": "loamz.agents.kernel.runtime",
    "AgentStore": "loamz.agents.stores",
    # context and middleware
    "ContextConfig": "loamz.agents.kernel.context",
    "CompactionPipeline": "loamz.agents.kernel.context",
    "SlidingWindowCompaction": "loamz.agents.kernel.context",
    "Middleware": "loamz.agents.kernel.middleware",
    "MiddlewarePipeline": "loamz.agents.kernel.middleware",
    # tools
    "Tool": "loamz.agents.kernel.tools",
    "ToolRisk": "loamz.agents.kernel.tools",
    "Toolbox": "loamz.agents.kernel.tools",
    "ToolExecutionResult": "loamz.agents.kernel.tools",
    "tool": "loamz.agents.kernel.tools",
    "ApprovalDecision": "loamz.agents.kernel.tools",
    "AutoApprove": "loamz.agents.kernel.tools",
    "DurableApproval": "loamz.agents.kernel.tools",
    "Skill": "loamz.agents.kernel.tools",
    # models and content
    "ChatModel": "loamz.agents.kernel.models",
    "GenerationOptions": "loamz.agents.kernel.models",
    "ReasoningEffort": "loamz.agents.kernel.models",
    "ChatMessage": "loamz.agents.kernel.types",
    "TextBlock": "loamz.agents.kernel.types",
    "create_model_client": "loamz.agents.providers.llm",
    "LLMFactory": "loamz.agents.providers.llm",
}

if TYPE_CHECKING:
    from loamz.agents.kernel.agents import (
        ChannelMemberAgent,
        ConditionalFlow,
        OrchestratorAgent,
        ParallelFlow,
        ReActAgent,
        SequentialFlow,
        SubAgentConfig,
        UserProxyAgent,
    )
    from loamz.agents.kernel.context import (
        CompactionPipeline,
        ContextConfig,
        SlidingWindowCompaction,
    )
    from loamz.agents.kernel.middleware import Middleware, MiddlewarePipeline
    from loamz.agents.kernel.models import ChatModel, GenerationOptions, ReasoningEffort
    from loamz.agents.kernel.runtime import RunOutcome, Runtime
    from loamz.agents.kernel.tools import (
        ApprovalDecision,
        AutoApprove,
        DurableApproval,
        Skill,
        Tool,
        Toolbox,
        ToolExecutionResult,
        ToolRisk,
        tool,
    )
    from loamz.agents.kernel.types import ChatMessage, TextBlock
    from loamz.agents.providers.llm import LLMFactory, create_model_client
    from loamz.agents.stores import AgentStore


async def connect(path: str | Path = "./.loamz/agents.db") -> AgentStore:
    """Open (creating it if there is none) the agent store in the SQLite file ``path``."""
    from loamz.agents.stores import AgentStore

    store = AgentStore.at(path)
    await store.start()
    return store


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'loamz.agents' has no attribute {name!r}")


__all__ = [
    "AgentStore",
    "ApprovalDecision",
    "AutoApprove",
    "ChannelMemberAgent",
    "ChatMessage",
    "ChatModel",
    "CompactionPipeline",
    "ConditionalFlow",
    "ContextConfig",
    "DurableApproval",
    "GenerationOptions",
    "LLMFactory",
    "Middleware",
    "MiddlewarePipeline",
    "OrchestratorAgent",
    "ParallelFlow",
    "ReActAgent",
    "ReasoningEffort",
    "RunOutcome",
    "Runtime",
    "SequentialFlow",
    "Skill",
    "SlidingWindowCompaction",
    "SubAgentConfig",
    "TextBlock",
    "Tool",
    "ToolExecutionResult",
    "ToolRisk",
    "Toolbox",
    "UserProxyAgent",
    "connect",
    "create_model_client",
    "tool",
]
