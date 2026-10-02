"""Compaction pipeline presets.

Split out of ``factory.py`` (which used to bundle this with agent
construction and the research-orchestrator topology — three unrelated
jobs in one file). This is compaction-specific configuration, not agent
construction, so it belongs beside the compaction strategies it presets.
"""

from __future__ import annotations

from substrate.context.compaction.pipeline import CompactionPipeline


def build_token_budget_pipeline(*, token_budget: int = 50_000) -> CompactionPipeline:
    """Compaction pipeline used by chat-facing agents: trims tool results and
    older tool-call groups before truncating outright, keeping recent
    context intact until the token budget is actually under pressure."""
    from substrate.context.compaction.selective_tool_call import SelectiveToolCallCompactionStrategy
    from substrate.context.compaction.token_budget_composed import TokenBudgetComposedStrategy
    from substrate.context.compaction.tool_result import ToolResultCompactionStrategy
    from substrate.context.compaction.truncation import TruncationStrategy

    return CompactionPipeline(
        [
            TokenBudgetComposedStrategy(
                strategies=[
                    ToolResultCompactionStrategy(max_chars=1500),
                    SelectiveToolCallCompactionStrategy(keep_recent_groups=5),
                    TruncationStrategy(max_chars=200_000),
                ],
                token_budget=token_budget,
            )
        ]
    )


__all__ = ["build_token_budget_pipeline"]
