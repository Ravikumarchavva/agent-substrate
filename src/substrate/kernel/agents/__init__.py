"""substrate.kernel.agents — agent types."""

from __future__ import annotations

from substrate.kernel.agents.base import BaseAgent
from substrate.kernel.agents.orchestrator import OrchestratorAgent, SubAgentConfig
from substrate.kernel.agents.proxy import UserProxyAgent
from substrate.kernel.agents.react import ReActAgent

__all__ = [
    "BaseAgent",
    "ReActAgent",
    "UserProxyAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
]
