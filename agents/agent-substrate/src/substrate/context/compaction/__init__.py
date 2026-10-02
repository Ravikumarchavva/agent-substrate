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

from substrate.context.protocols import CompactionStrategy
from substrate.context.compaction.coordinator import (
    CompactionCoordinator,
    DefaultCompactionCoordinator,
)
from substrate.context.compaction.pipeline import CompactionPipeline
from substrate.context.compaction.presets import build_token_budget_pipeline
from substrate.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from substrate.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.context.compaction.summarization import SummarizationCompaction
from substrate.context.compaction.threshold import ThresholdCheckpointStrategy
from substrate.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)
from substrate.context.compaction.tool_result import ToolResultCompactionStrategy
from substrate.context.compaction.truncation import TruncationStrategy

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
