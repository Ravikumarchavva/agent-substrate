"""substrate.agents.context — agent context management and history providers."""

from __future__ import annotations

from .history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
    InMemoryHistoryProvider,
    project_messages,
)
from .local_history import LocalFilesystemHistoryProvider
from .builder import DefaultContextBuilder
from .compaction import (
    CompactionStrategy,
    CompactionPipeline,
    CompactionCoordinator,
    DefaultCompactionCoordinator,
    SlidingWindowCompaction,
    SummarizationCompaction,
    ToolResultCompactionStrategy,
    SelectiveToolCallCompactionStrategy,
    TruncationStrategy,
    TokenBudgetComposedStrategy,
)
from .context import AgentContext, ContextConfig

__all__ = [
    "HistoryProvider",
    "project_messages",
    "InMemoryHistoryProvider",
    "LocalFilesystemHistoryProvider",
    "DefaultHistoryResolver",
    "AncestryCheckpointResolver",
    "DefaultContextBuilder",
    "CompactionStrategy",
    "CompactionPipeline",
    "CompactionCoordinator",
    "DefaultCompactionCoordinator",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
    "AgentContext",
    "ContextConfig",
]
