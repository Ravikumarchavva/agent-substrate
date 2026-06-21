"""substrate.agents.core — agent types."""

from __future__ import annotations

from substrate.agents.core.react import ReActAgent
from substrate.agents.core.proxy import UserProxyAgent
from substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
from substrate.agents.core.information_agent import InformationAgent
from substrate.agents.core.personal_feed_agent import PersonalFeedAgent

__all__ = [
    "ReActAgent",
    "UserProxyAgent",
    "OrchestratorAgent",
    "SubAgentConfig",
    "InformationAgent",
    "PersonalFeedAgent",
]
