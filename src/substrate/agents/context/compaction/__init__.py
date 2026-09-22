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

from substrate.kernel.agent.context import CompactionStrategy

from substrate.agents.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.agents.context.compaction.summarization import SummarizationCompaction
from substrate.agents.context.compaction.tool_result import ToolResultCompactionStrategy
from substrate.agents.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from substrate.agents.context.compaction.truncation import TruncationStrategy
from substrate.agents.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)
from substrate.agents.context.compaction.pipeline import CompactionPipeline
from substrate.agents.context.compaction.coordinator import (
    CompactionCoordinator,
    DefaultCompactionCoordinator,
)
from substrate.agents.context.compaction.threshold import ThresholdCheckpointStrategy
from substrate.agents.context.compaction.presets import build_token_budget_pipeline

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

