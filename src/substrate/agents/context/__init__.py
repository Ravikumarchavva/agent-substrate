"""substrate.agents.context — agent context management and history providers."""

from __future__ import annotations

from .history import (
    AncestryCheckpointResolver,
    CheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
    HistoryResolver,
    InMemoryHistoryProvider,
)
from .builder import DefaultContextBuilder
from .compaction import (
    CompactionStrategy,
    CompactionPipeline,
    DefaultCompactionCoordinator,
    SlidingWindowCompaction,
    SummarizationCompaction,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
)
from .context import AgentContext, AgentContextProtocol, ContextConfig
from .workspace import InMemoryWorkspaceStore

__all__ = [
    "HistoryProvider",
    "HistoryResolver",
    "CheckpointResolver",
    "InMemoryHistoryProvider",
    "DefaultHistoryResolver",
    "AncestryCheckpointResolver",
    "InMemoryWorkspaceStore",
    "DefaultContextBuilder",
    "CompactionStrategy",
    "CompactionPipeline",
    "DefaultCompactionCoordinator",
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
