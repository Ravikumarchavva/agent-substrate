"""substrate.agents — Agents: RoutedAgent and @handle, ReAct, orchestrator, proxy, flows, spawn limits."""

from __future__ import annotations

from substrate.agents.base import (
    BaseAgent,
)
from substrate.agents.channel import (
    PASS,
    ChannelMemberAgent,
    ChannelMemberConfig,
)
from substrate.agents.flows import (
    ConditionalFlow,
    ParallelFlow,
    SequentialFlow,
)
from substrate.agents.orchestrator import (
    OrchestratorAgent,
    SubAgentConfig,
)
from substrate.agents.proxy import (
    UserProxyAgent,
)
from substrate.agents.react import (
    ReActAgent,
)
from substrate.agents.spawn import (
    SpawnTracker,
)

__all__ = [
    "BaseAgent",
    "ChannelMemberAgent",
    "ChannelMemberConfig",
    "ConditionalFlow",
    "OrchestratorAgent",
    "PASS",
    "ParallelFlow",
    "ReActAgent",
    "SequentialFlow",
    "SpawnTracker",
    "SubAgentConfig",
    "UserProxyAgent",
]
