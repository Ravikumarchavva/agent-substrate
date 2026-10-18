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

from loamz.agents.kernel.context.compaction.coordinator import (
    CompactionCoordinator,
    DefaultCompactionCoordinator,
)
from loamz.agents.kernel.context.compaction.pipeline import CompactionPipeline
from loamz.agents.kernel.context.compaction.presets import build_token_budget_pipeline
from loamz.agents.kernel.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from loamz.agents.kernel.context.compaction.sliding_window import SlidingWindowCompaction
from loamz.agents.kernel.context.compaction.summarization import SummarizationCompaction
from loamz.agents.kernel.context.compaction.threshold import ThresholdCheckpointStrategy
from loamz.agents.kernel.context.compaction.token_budget_composed import TokenBudgetComposedStrategy
from loamz.agents.kernel.context.compaction.tool_result import ToolResultCompactionStrategy
from loamz.agents.kernel.context.compaction.truncation import TruncationStrategy
from loamz.agents.kernel.context.protocols import CompactionStrategy

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
