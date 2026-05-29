from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import split_system
from ravi.kernel.context.compaction import CompactionStrategy, Trigger
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import CachedHistoryProvider, HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient


class CachedStrategy(CompactionStrategy):
    """Context strategy that reads recent history from a cached provider."""

    trigger = Trigger.BEFORE_LLM_CALL

    def __init__(
        self,
        provider: "CachedHistoryProvider",
        recent_n: int = 10,
    ) -> None:
        if recent_n < 1:
            raise ValueError("recent_n must be >= 1")
        self._provider = provider
        self.recent_n = recent_n

    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        all_messages = await self._provider.load_messages(session_id)
        system_msg, rest = split_system(all_messages)
        windowed = rest[-self.recent_n:] if len(rest) > self.recent_n else list(rest)
        if system_msg is not None:
            return [system_msg, *windowed]
        return windowed

    def __repr__(self) -> str:
        return f"<CachedStrategy(recent_n={self.recent_n})>"
