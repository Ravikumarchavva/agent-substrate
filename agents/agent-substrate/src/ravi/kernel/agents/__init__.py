"""ravi.kernel.agents — Agent result types and configuration.

Only pure value types live here. The ``ActorAgent`` base class lives in
:mod:`ravi.fabric.actors.actor` (L1 — it depends on the runtime). Concrete
agents (``AssistantAgent``) live in :mod:`ravi.reasoning.agents`; multi-agent
coordinators live in :mod:`ravi.orchestration.agents`.
"""

from ravi.kernel.agents.config import AgentConfig
from ravi.kernel.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)

__all__ = [
    "AgentConfig",
    "AgentRunResult",
    "AggregatedUsage",
    "RunStatus",
    "StepResult",
    "ToolCallRecord",
]
