"""Compaction strategies for agent conversation history.

Strategy                             Aggressiveness  Preserves context  Requires LLM
───────────────────────────────────  ──────────────  ─────────────────  ────────────
ToolResultCompactionStrategy         Low             High               No
SelectiveToolCallCompactionStrategy  Low–Medium      Medium             No
SummarizationCompaction              Medium          Medium             Yes
SlidingWindowCompaction              High            Low                No
TruncationStrategy                   High            Low                No
TokenBudgetComposedStrategy          Configurable    Depends            Depends
"""

from __future__ import annotations

from substrate.kernel.abstractions.agent.context import CompactionStrategy
from substrate.kernel.context.compaction.coordinator import (
    CompactionCoordinator,
    DefaultCompactionCoordinator,
)
from substrate.kernel.context.compaction.pipeline import CompactionPipeline
from substrate.kernel.context.compaction.presets import build_token_budget_pipeline
from substrate.kernel.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from substrate.kernel.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.kernel.context.compaction.summarization import SummarizationCompaction
from substrate.kernel.context.compaction.threshold import ThresholdCheckpointStrategy
from substrate.kernel.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)
from substrate.kernel.context.compaction.tool_result import ToolResultCompactionStrategy
from substrate.kernel.context.compaction.truncation import TruncationStrategy

__all__ = [
    "CompactionStrategy",
    "CompactionPipeline",
    "CompactionCoordinator",
    "DefaultCompactionCoordinator",
    "ThresholdCheckpointStrategy",
    "build_token_budget_pipeline",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
]

