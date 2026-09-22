"""substrate.agents.core — agent types."""

from __future__ import annotations

from substrate.agents.core.base import BaseAgent
from substrate.agents.core.react import ReActAgent
from substrate.agents.core.proxy import UserProxyAgent
from substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig

__all__ = [
    "BaseAgent",
    "ReActAgent",
    "UserProxyAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
]
