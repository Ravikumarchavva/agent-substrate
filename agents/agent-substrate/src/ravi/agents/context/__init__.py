"""ravi.agents.context — agent context management and history providers."""

from __future__ import annotations

from .history import HistoryProvider, InMemoryHistoryProvider
from .compaction import (
    CompactionStrategy,
    SlidingWindowCompaction,
    SummarizationCompaction,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
)
from .context import AgentContext, AgentContextProtocol, ContextConfig

__all__ = [
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "CompactionStrategy",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
    "AgentContext",
    "AgentContextProtocol",
    "ContextConfig",
]
