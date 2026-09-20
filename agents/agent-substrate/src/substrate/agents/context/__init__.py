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
from substrate.capabilities.history.local_history import LocalFilesystemHistoryProvider
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
from .workspace import InMemoryWorkspaceStore, LocalFilesystemWorkspaceStore

__all__ = [
    "HistoryProvider",
    "HistoryResolver",
    "CheckpointResolver",
    "InMemoryHistoryProvider",
    "LocalFilesystemHistoryProvider",
    "DefaultHistoryResolver",
    "AncestryCheckpointResolver",
    "InMemoryWorkspaceStore",
    "LocalFilesystemWorkspaceStore",
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
