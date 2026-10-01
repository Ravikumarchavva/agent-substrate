"""substrate.agents.context — context window assembly and compaction.

History providers moved to ``agents.storage`` — general-purpose kernel
``HistoryProvider`` implementations belong beside the other in-memory/local
kernel-Protocol defaults, not under "context" (context window ASSEMBLY,
not history STORAGE, is this package's job).
"""

from __future__ import annotations

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
