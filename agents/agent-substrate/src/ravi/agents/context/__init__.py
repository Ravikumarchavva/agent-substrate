"""ravi.agents.context — agent context management and history providers."""

from __future__ import annotations

from .history import HistoryProvider, InMemoryHistoryProvider
from .compaction import (
    CompactionStrategy,
    SlidingWindowStrategy,
    SummarizationStrategy,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
    SlidingWindowCompaction,
    SummarizationCompaction,
)
from .context import AgentContext, AgentContextProtocol, ContextConfig

__all__ = [
    "HistoryProvider",
    "InMemoryHistoryProvider",
    "CompactionStrategy",
    "SlidingWindowStrategy",
    "SummarizationStrategy",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "AgentContext",
    "AgentContextProtocol",
    "ContextConfig",
]
