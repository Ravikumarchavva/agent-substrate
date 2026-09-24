"""TokenBudgetComposedStrategy — applies child strategies until within token budget."""

from __future__ import annotations

from substrate.agents.context.tokens import estimate_tokens
from substrate.kernel.core.content import ChatMessage
from substrate.kernel.agent.context import CompactionStrategy
from substrate.logger import setup_logging

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
            raise ValueError(
                "TokenBudgetComposedStrategy requires at least one strategy"
            )
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
        """Build a strategy whose budget is derived from the model's context window."""
        from substrate.agents.llm.models import get_context_length

        context_length = get_context_length(model_name, default=default_context_length)
        token_budget = int(context_length * trigger_ratio)
        return cls(
            strategies=strategies,
            token_budget=token_budget,
            chars_per_token=chars_per_token,
        )

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        current = raw_history

        if estimate_tokens(current, self._cpt) <= self._budget:
            return current

        for strategy in self._strategies:
            current = await strategy.compact(current)
            tokens = estimate_tokens(current, self._cpt)
            if tokens <= self._budget:
                return current

        logger.warning(
            "TokenBudgetComposedStrategy: all strategies exhausted; "
            "estimated %d tokens still exceeds budget %d",
            estimate_tokens(current, self._cpt),
            self._budget,
        )
        return current


__all__ = ["TokenBudgetComposedStrategy"]
