"""Compaction strategies for agent conversation history.

Strategy                        Aggressiveness  Preserves context  Requires LLM
──────────────────────────────  ──────────────  ─────────────────  ────────────
ToolResultCompactionStrategy    Low             High               No
SelectiveToolCallCompactionStrategy  Low–Medium  Medium            No
SummarizationStrategy           Medium          Medium             Yes
SlidingWindowStrategy           High            Low                No
TruncationStrategy              High            Low                No
TokenBudgetComposedStrategy     Configurable    Depends            Depends
"""

from __future__ import annotations

from ravi.kernel.context import CompactionStrategy

from ravi.agents.context.compaction.sliding_window import SlidingWindowStrategy
from ravi.agents.context.compaction.summarization import SummarizationStrategy
from ravi.agents.context.compaction.tool_result import ToolResultCompactionStrategy
from ravi.agents.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from ravi.agents.context.compaction.truncation import TruncationStrategy
from ravi.agents.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)

# Backward-compatible aliases (old names still work)
SlidingWindowCompaction = SlidingWindowStrategy
SummarizationCompaction = SummarizationStrategy

__all__ = [
    "CompactionStrategy",
    # Strategies
    "SlidingWindowStrategy",
    "SummarizationStrategy",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
    # Aliases
    "SlidingWindowCompaction",
    "SummarizationCompaction",
]
