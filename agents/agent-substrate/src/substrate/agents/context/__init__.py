"""substrate.agents.context — agent context management and history providers."""

from __future__ import annotations

from .history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
    InMemoryHistoryProvider,
    project_messages,
)
from substrate.capabilities.history.local_history import LocalFilesystemHistoryProvider
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
from .workspace import InMemoryWorkspaceStore, LocalFilesystemWorkspaceStore

__all__ = [
    "HistoryProvider",
    "project_messages",
    "InMemoryHistoryProvider",
    "LocalFilesystemHistoryProvider",
    "DefaultHistoryResolver",
    "AncestryCheckpointResolver",
    "InMemoryWorkspaceStore",
    "LocalFilesystemWorkspaceStore",
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
