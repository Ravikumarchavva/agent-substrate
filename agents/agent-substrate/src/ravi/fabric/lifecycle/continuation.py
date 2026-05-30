from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ravi.kernel import AgentId


@dataclass(frozen=True)
class Continuation:
    """Frozen execution state for a suspended agent.

    Used for Human-in-the-Loop escalations: the execution graph stops,
    emits this continuation, and waits for a human to resume it.
    """

    id: str
    agent_id: AgentId
    reason: str
    state_snapshot: dict[str, Any]
    context_metadata: dict[str, str]
