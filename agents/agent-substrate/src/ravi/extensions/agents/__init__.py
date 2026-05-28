"""ravi.extensions.agents — Concrete agent implementations.

Importing this package triggers ``@register_agent`` decorators on every
built-in agent variant.
"""

from __future__ import annotations

from ravi.extensions.agents.assistant.agent import AssistantAgent
from ravi.extensions.agents.user_proxy.agent import UserProxyAgent
from ravi.extensions.agents.runtime.agent import RuntimeAgent
from ravi.extensions.agents.orchestrator.agent import OrchestratorAgent
from ravi.extensions.agents.flow.agent import (
    BaseFlow,
    ConditionalFlow,
    ParallelFlow,
    SequentialFlow,
)
from ravi.extensions.agents.graph.agent import FlowEdge, FlowGraph, FlowNode

__all__ = [
    "AssistantAgent",
    "UserProxyAgent",
    "RuntimeAgent",
    "OrchestratorAgent",
    "BaseFlow",
    "SequentialFlow",
    "ParallelFlow",
    "ConditionalFlow",
    "FlowGraph",
    "FlowNode",
    "FlowEdge",
]
