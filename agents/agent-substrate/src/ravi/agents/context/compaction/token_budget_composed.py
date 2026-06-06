"""TokenBudgetComposedStrategy — applies child strategies until within token budget."""

from __future__ import annotations

from ravi.kernel import Message
from ravi.kernel.content import ChatMessage, TextBlock, ToolResultBlock
from ravi.kernel.context import CompactionStrategy
from ravi.logger import setup_logging

logger = setup_logging()


class TokenBudgetComposedStrategy:
    """Applies child strategies in order until history fits within *token_budget*.

    Aggressiveness: Configurable — determined by the child strategies provided.
    Preserves context: Depends on child strategies.
    Requires LLM: Depends on child strategies.

    On each ``compact()`` call the strategy checks the estimated token count.
    If already within budget it returns immediately.  Otherwise it applies
    each child strategy in sequence, re-checking after each one, stopping as
    soon as the budget is met.  If all strategies are exhausted and the history
    is still over budget, the compacted result from the last strategy is
    returned and a warning is logged.

    Token estimation uses character counting with a configurable
    ``chars_per_token`` ratio (default 4.0 — approximate for English text).

    Example::

        from ravi.agents.context.compaction import (
            TokenBudgetComposedStrategy,
            ToolResultCompactionStrategy,
            SelectiveToolCallCompactionStrategy,
            SummarizationStrategy,
            TruncationStrategy,
        )

        strategy = TokenBudgetComposedStrategy(
            strategies=[
                ToolResultCompactionStrategy(max_chars=300),
                SelectiveToolCallCompactionStrategy(keep_recent_groups=3),
                SummarizationStrategy(model=cheap_model),
                TruncationStrategy(max_messages=40),
            ],
            token_budget=8_000,
        )

    Args:
        strategies:      Ordered list of strategies to apply.  Start with
                         low-aggressiveness ones and end with high-aggressiveness.
        token_budget:    Target maximum token count (estimated).
        chars_per_token: Characters-per-token ratio for the estimation.
    """

    def __init__(
        self,
        strategies: list[CompactionStrategy],
        token_budget: int,
        chars_per_token: float = 4.0,
    ) -> None:
        if not strategies:
            raise ValueError("TokenBudgetComposedStrategy requires at least one strategy")
        self._strategies = strategies
        self._budget = token_budget
        self._cpt = chars_per_token

    @classmethod
    def from_model(
        cls,
        model_name: str,
        strategies: list[CompactionStrategy],
        trigger_ratio: float = 0.80,
        chars_per_token: float = 4.0,
        default_context_length: int = 128_000,
    ) -> "TokenBudgetComposedStrategy":
        """Build a strategy whose budget is derived from the model's context window.

        Args:
            model_name:             Model name or alias (e.g. ``"gpt-4o"``).
            strategies:             Ordered child strategies (least → most aggressive).
            trigger_ratio:          Compact when this fraction of the context is used.
                                    Default 0.80 (80%).
            chars_per_token:        Estimation ratio passed to ``_estimate_tokens``.
            default_context_length: Fallback when the model is not in the registry.

        Example::

            from ravi.agents.llm.models import get_context_length
            from ravi.agents.context.compaction import (
                TokenBudgetComposedStrategy,
                ToolResultCompactionStrategy,
                SummarizationStrategy,
                TruncationStrategy,
            )

            context_length = get_context_length("gpt-4o")  # 128_000
            strategy = TokenBudgetComposedStrategy.from_model(
                "gpt-4o",
                strategies=[
                    ToolResultCompactionStrategy(max_chars=500),
                    SummarizationStrategy(
                        model=cheap_model,
                        recent_token_budget=int(context_length * 0.40),
                    ),
                    TruncationStrategy(max_messages=200),
                ],
                trigger_ratio=0.80,
            )
        """
        from ravi.agents.llm.models import get_context_length

        context_length = get_context_length(model_name, default=default_context_length)
        token_budget = int(context_length * trigger_ratio)
        return cls(
            strategies=strategies,
            token_budget=token_budget,
            chars_per_token=chars_per_token,
        )

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        current = raw_history

        if self._estimate_tokens(current) <= self._budget:
            return current

        for strategy in self._strategies:
            current = await strategy.compact(current)
            tokens = self._estimate_tokens(current)
            if tokens <= self._budget:
                return current

        logger.warning(
            "TokenBudgetComposedStrategy: all strategies exhausted; "
            "estimated %d tokens still exceeds budget %d",
            self._estimate_tokens(current),
            self._budget,
        )
        return current

    def _estimate_tokens(self, history: list[Message]) -> int:
        total_chars = 0
        for msg in history:
            if not isinstance(msg.payload, ChatMessage):
                continue
            for block in msg.payload.content:
                if isinstance(block, TextBlock):
                    total_chars += len(block.text)
                elif isinstance(block, ToolResultBlock):
                    for inner in block.content:
                        if isinstance(inner, TextBlock):
                            total_chars += len(inner.text)
        return max(1, int(total_chars / self._cpt))


__all__ = ["TokenBudgetComposedStrategy"]
