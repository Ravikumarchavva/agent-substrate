from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import split_system
from ravi.fabric.context.compaction import CompactionStrategy, Trigger
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient


class SlidingWindowStrategy(CompactionStrategy):
    """Keep the system prompt plus the last *max_messages* non-system messages."""

    trigger = Trigger.BEFORE_LLM_CALL

    def __init__(self, max_messages: int = 40) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be >= 1")
        self.max_messages = max_messages

    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = split_system(messages)
        windowed = rest[-self.max_messages:] if len(rest) > self.max_messages else rest
        if system_msg is not None:
            return [system_msg, *windowed]
        return windowed

    def __repr__(self) -> str:
        return f"<SlidingWindowStrategy(max_messages={self.max_messages})>"

