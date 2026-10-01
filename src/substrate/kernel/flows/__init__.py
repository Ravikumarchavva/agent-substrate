"""substrate.agents.flows — kernel-native agent orchestration flows."""

from __future__ import annotations

from substrate.agents.flows.agent import (
    ConditionalFlow,
    ParallelFlow,
    SequentialFlow,
)

__all__ = ["SequentialFlow", "ParallelFlow", "ConditionalFlow"]
