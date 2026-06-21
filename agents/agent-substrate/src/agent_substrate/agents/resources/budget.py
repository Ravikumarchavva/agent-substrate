from __future__ import annotations

from dataclasses import dataclass, field

from agent_substrate.kernel.core.errors import BudgetExhaustedError


@dataclass
class ExecutionTracker:
    """Enforces token, cost, or turn limits on an agent's execution loop.

    Call ``consume()`` after each LLM turn; it raises ``BudgetExhaustedError``
    as soon as any limit is breached.

    Limits of ``None`` mean unlimited.
    """

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_turns: int | None = None

    _used_tokens: int = field(default=0, init=False, repr=False)
    _used_cost: float = field(default=0.0, init=False, repr=False)
    _used_turns: int = field(default=0, init=False, repr=False)

    def consume(self, tokens: int = 0, cost: float = 0.0, turns: int = 0) -> None:
        """Record resource usage and raise if any limit is exceeded."""
        self._used_tokens += tokens
        self._used_cost += cost
        self._used_turns += turns
        self._check()

    def _check(self) -> None:
        if self.max_tokens is not None and self._used_tokens > self.max_tokens:
            raise BudgetExhaustedError(
                f"Token budget exceeded: {self._used_tokens} > {self.max_tokens}"
            )
        if self.max_cost_usd is not None and self._used_cost > self.max_cost_usd:
            raise BudgetExhaustedError(
                f"Cost budget exceeded: ${self._used_cost:.4f} > ${self.max_cost_usd:.4f}"
            )
        if self.max_turns is not None and self._used_turns > self.max_turns:
            raise BudgetExhaustedError(
                f"Turn limit exceeded: {self._used_turns} > {self.max_turns}"
            )

    @property
    def used_tokens(self) -> int:
        return self._used_tokens

    @property
    def used_cost(self) -> float:
        return self._used_cost

    @property
    def used_turns(self) -> int:
        return self._used_turns
