"""ravi.agents.context — agent context management and history providers."""

from __future__ import annotations

from .history import HistoryProvider, InMemoryHistoryProvider
from .compaction import CompactionStrategy, SlidingWindowCompaction
from .context import AgentContext, AgentContextProtocol, DefaultAgentContext

__all__ = [
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "CompactionStrategy",
    "SlidingWindowCompaction",
    "AgentContext",
    "AgentContextProtocol",
    "DefaultAgentContext",
]
