from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import estimate_tokens, split_system
from ravi.fabric.agents_base.compaction import CompactionStrategy, Trigger
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient


class TokenBudgetStrategy(CompactionStrategy):
    """Drop oldest non-system messages until the token budget is met."""

    trigger = Trigger.BEFORE_LLM_CALL

    def __init__(self, max_tokens: int = 8_000) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        self.max_tokens = max_tokens

    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = split_system(messages)

        async def token_count(msgs: List[BaseClientMessage]) -> int:
            if model_client is not None and hasattr(model_client, "count_tokens"):
                return await model_client.count_tokens(msgs)  # type: ignore[attr-defined]
            return estimate_tokens(msgs)

        trimmed = list(rest)
        while trimmed and await token_count(trimmed) > self.max_tokens:
            trimmed.pop(0)

        if system_msg is not None:
            return [system_msg, *trimmed]
        return trimmed

    def __repr__(self) -> str:
        return f"<TokenBudgetStrategy(max_tokens={self.max_tokens})>"
