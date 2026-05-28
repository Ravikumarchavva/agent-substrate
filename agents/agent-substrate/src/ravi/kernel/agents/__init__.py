"""ravi.kernel.agents — Agent contracts and result types.

Only abstractions live here. Concrete agent implementations
(``AssistantAgent``, ``UserProxyAgent``, ``OrchestratorAgent``, flows) live in
:mod:`ravi.extensions.agents`.
"""

from ravi.kernel.agents.actor import ActorAgent, StreamChannel, StreamEnvelope
from ravi.kernel.agents.config import AgentConfig
from ravi.kernel.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)

__all__ = [
    "ActorAgent",
    "StreamChannel",
    "StreamEnvelope",
    "AgentConfig",
    "AgentRunResult",
    "AggregatedUsage",
    "RunStatus",
    "StepResult",
    "ToolCallRecord",
]
