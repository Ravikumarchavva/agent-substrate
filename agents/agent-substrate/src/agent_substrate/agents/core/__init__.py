"""agent_substrate.agents.core — agent types."""

from __future__ import annotations

from agent_substrate.agents.core.react import ReActAgent
from agent_substrate.agents.core.proxy import UserProxyAgent
from agent_substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
from agent_substrate.agents.core.information_agent import InformationAgent
from agent_substrate.agents.core.personal_feed_agent import PersonalFeedAgent

__all__ = [
    "ReActAgent",
    "UserProxyAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
    "InformationAgent",
    "PersonalFeedAgent",
]
