"""CompactionPipeline — chains multiple CompactionStrategies in sequence.

Each strategy's output is fed as input to the next.  This lets you compose
lightweight strategies without writing a custom class::

    pipeline = CompactionPipeline([
        ToolResultCompactionStrategy(),
        SlidingWindowCompaction(max_messages=40),
    ])
    context = ContextConfig(history, pipeline)

When only one strategy is needed, pass it directly — no need to wrap it.
"""

from __future__ import annotations

from agent_substrate.kernel.agent.context import CompactionStrategy
from agent_substrate.kernel.core.content import ChatMessage


class CompactionPipeline:
    """Run multiple :class:`CompactionStrategy` instances in order.

    The output of each strategy becomes the input of the next, producing
    a single compacted history without requiring callers to chain calls
    manually.  An empty pipeline is a no-op (returns raw history unchanged).
    """

    def __init__(self, strategies: list[CompactionStrategy]) -> None:
        self._strategies = list(strategies)

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        """Apply all strategies in sequence, returning the final result."""
        window = raw_history
        for strategy in self._strategies:
            window = await strategy.compact(window)
        return window

    def __repr__(self) -> str:
        names = [type(s).__name__ for s in self._strategies]
        return f"CompactionPipeline([{', '.join(names)}])"
