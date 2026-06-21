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

from agent_substrate.kernel.agent.context import CompactionStrategy

from agent_substrate.agents.context.compaction.sliding_window import SlidingWindowCompaction
from agent_substrate.agents.context.compaction.summarization import SummarizationCompaction
from agent_substrate.agents.context.compaction.tool_result import ToolResultCompactionStrategy
from agent_substrate.agents.context.compaction.selective_tool_call import (
    SelectiveToolCallCompactionStrategy,
)
from agent_substrate.agents.context.compaction.truncation import TruncationStrategy
from agent_substrate.agents.context.compaction.token_budget_composed import (
    TokenBudgetComposedStrategy,
)
from agent_substrate.agents.context.compaction.pipeline import CompactionPipeline

__all__ = [
    "CompactionStrategy",
    "CompactionPipeline",
    "SlidingWindowCompaction",
    "SummarizationCompaction",
    "ToolResultCompactionStrategy",
    "SelectiveToolCallCompactionStrategy",
    "TruncationStrategy",
    "TokenBudgetComposedStrategy",
]
