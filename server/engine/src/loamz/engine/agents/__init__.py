"""Loamz Agents: durable agents, tools and an agent server, built on Loamz Data.

    from loamz.engine.agents import ReActAgent, Runtime, ToolRisk, connect, create_model_client, tool

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def shout(text: str) -> str:
        "Upper-case the text."
        return text.upper()

    store = await connect("./.loamz/agents.db")            # SQLite; AgentStore(postgres(dsn)) for many machines
    agent = ReActAgent("bot", model=create_model_client("gpt-4o-mini", api_keys=keys), tools=[shout])
    async with Runtime(store, owns_store=True) as runtime:
        print((await runtime.run(agent, "Say hi loudly")).output)

The names here are the ones nearly every program wants; everything else is imported from where it lives
(``loamz.engine.agents.kernel.*``, ``loamz.engine.agents.stores``, ``loamz.engine.agents.providers``, ``loamz.engine.agents.toolkit``).
Importing ``loamz.engine.agents`` imports nothing heavy: each name loads on first use.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

# A library installs no handler of its own: the application decides where records go (``bootstrap.logging``).
logging.getLogger("loamz.engine.agents").addHandler(logging.NullHandler())

_LAZY: dict[str, str] = {
    # agents
    "ReActAgent": "loamz.engine.agents.kernel.agents",
    "OrchestratorAgent": "loamz.engine.agents.kernel.agents",
    "SubAgentConfig": "loamz.engine.agents.kernel.agents",
    "UserProxyAgent": "loamz.engine.agents.kernel.agents",
    "ChannelMemberAgent": "loamz.engine.agents.kernel.agents",
    "SequentialFlow": "loamz.engine.agents.kernel.agents",
    "ParallelFlow": "loamz.engine.agents.kernel.agents",
    "ConditionalFlow": "loamz.engine.agents.kernel.agents",
    # running them
    "Runtime": "loamz.engine.agents.kernel.runtime",
    "RunOutcome": "loamz.engine.agents.kernel.runtime",
    "AgentStore": "loamz.engine.agents.stores",
    # context and middleware
    "ContextConfig": "loamz.engine.agents.kernel.context",
    "CompactionPipeline": "loamz.engine.agents.kernel.context",
    "SlidingWindowCompaction": "loamz.engine.agents.kernel.context",
    "Middleware": "loamz.engine.agents.kernel.middleware",
    "MiddlewarePipeline": "loamz.engine.agents.kernel.middleware",
    # tools
    "Tool": "loamz.engine.agents.kernel.tools",
    "ToolRisk": "loamz.engine.agents.kernel.tools",
    "Toolbox": "loamz.engine.agents.kernel.tools",
    "ToolExecutionResult": "loamz.engine.agents.kernel.tools",
    "tool": "loamz.engine.agents.kernel.tools",
    "ApprovalDecision": "loamz.engine.agents.kernel.tools",
    "AutoApprove": "loamz.engine.agents.kernel.tools",
    "DurableApproval": "loamz.engine.agents.kernel.tools",
    "Skill": "loamz.engine.agents.kernel.tools",
    # models and content
    "ChatModel": "loamz.engine.agents.kernel.models",
    "GenerationOptions": "loamz.engine.agents.kernel.models",
    "ReasoningEffort": "loamz.engine.agents.kernel.models",
    "ChatMessage": "loamz.engine.agents.kernel.types",
    "TextBlock": "loamz.engine.agents.kernel.types",
    "create_model_client": "loamz.engine.agents.providers.llm",
    "LLMFactory": "loamz.engine.agents.providers.llm",
}

if TYPE_CHECKING:
    from loamz.engine.agents.kernel.agents import (
        ChannelMemberAgent,
        ConditionalFlow,
        OrchestratorAgent,
        ParallelFlow,
        ReActAgent,
        SequentialFlow,
        SubAgentConfig,
        UserProxyAgent,
    )
    from loamz.engine.agents.kernel.context import (
        CompactionPipeline,
        ContextConfig,
        SlidingWindowCompaction,
    )
    from loamz.engine.agents.kernel.middleware import Middleware, MiddlewarePipeline
    from loamz.engine.agents.kernel.models import ChatModel, GenerationOptions, ReasoningEffort
    from loamz.engine.agents.kernel.runtime import RunOutcome, Runtime
    from loamz.engine.agents.kernel.tools import (
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
    from loamz.engine.agents.kernel.types import ChatMessage, TextBlock
    from loamz.engine.agents.providers.llm import LLMFactory, create_model_client
    from loamz.engine.agents.stores import AgentStore


async def connect(path: str | Path = "./.loamz/agents.db") -> AgentStore:
    """Open (creating it if there is none) the agent store in the SQLite file ``path``."""
    from loamz.engine.agents.stores import AgentStore

    store = AgentStore.at(path)
    await store.start()
    return store


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'loamz.engine.agents' has no attribute {name!r}")


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
