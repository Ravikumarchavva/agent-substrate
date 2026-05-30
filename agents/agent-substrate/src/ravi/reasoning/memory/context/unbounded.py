from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.fabric.agents_base.compaction import CompactionStrategy, Trigger
from ravi.kernel.messages.base_message import BaseClientMessage

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient


class UnboundedStrategy(CompactionStrategy):
    """Pass-through strategy that returns all messages unchanged."""

    trigger = Trigger.BEFORE_LLM_CALL

    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        return messages

    def __repr__(self) -> str:
        return "<UnboundedStrategy>"
