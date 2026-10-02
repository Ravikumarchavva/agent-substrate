"""substrate.context — The context window: building it from a thread, compacting it, counting its tokens."""

from __future__ import annotations

from substrate.context.builder import (
    DefaultContextBuilder,
)
from substrate.context.compaction.coordinator import (
    CompactionCoordinator,
    DefaultCompactionCoordinator,
)
from substrate.context.compaction.pipeline import (
    CompactionPipeline,
)
from substrate.context.compaction.presets import (
    build_token_budget_pipeline,
)
from substrate.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from substrate.context.compaction.sliding_window import (
    SlidingWindowCompaction,
)
from substrate.context.compaction.summarization import (
    SummarizationCompaction,
)
from substrate.context.compaction.threshold import (
    ThresholdCheckpointStrategy,
)
from substrate.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)
from substrate.context.compaction.tool_result import (
    ToolResultCompactionStrategy,
)
from substrate.context.compaction.truncation import (
    TruncationStrategy,
)
from substrate.context.context import (
    AgentContext,
    ContextConfig,
)
from substrate.context.history import (
    AncestryCheckpointResolver,
    DefaultHistoryResolver,
    HistoryProvider,
    project_messages,
)
from substrate.context.protocols import (
    CompactionContext,
    CompactionPhase,
    CompactionResult,
    CompactionStrategy,
    ContextBuilder,
    ContextWindow,
)

__all__ = [
    "AgentContext",
    "AncestryCheckpointResolver",
    "CompactionContext",
    "CompactionCoordinator",
    "CompactionPhase",
    "CompactionPipeline",
    "CompactionResult",
    "CompactionStrategy",
    "ContextBuilder",
    "ContextConfig",
    "ContextWindow",
    "DefaultCompactionCoordinator",
    "DefaultContextBuilder",
    "DefaultHistoryResolver",
    "HistoryProvider",
    "SelectiveToolCallCompactionStrategy",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ThresholdCheckpointStrategy",
    "TokenBudgetComposedStrategy",
    "ToolResultCompactionStrategy",
    "TruncationStrategy",
    "build_token_budget_pipeline",
    "project_messages",
]
