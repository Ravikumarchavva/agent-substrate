"""ModelContext — first-class context manager orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.kernel.context.compaction import CompactionStrategy, Trigger

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient
    from ravi.kernel.messages.base_message import BaseClientMessage


class ModelContext:
    """Manages an agent's conversation context using history provider and compaction strategies.

    ModelContext is the container and orchestrator. It holds a backing HistoryProvider
    and a list of CompactionStrategy objects, and executes them at appropriate points
    in the agent loop (before LLM calls, after runs, on token limit).
    """

    def __init__(
        self,
        history: "HistoryProvider",
        compaction_strategies: Optional[List[CompactionStrategy]] = None,
    ) -> None:
        self.history = history
        self.compaction_strategies = compaction_strategies or []

    async def build(
        self,
        session_id: str,
        raw_messages: List[BaseClientMessage],
        current_input: Optional[str] = None,
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        """Apply all Trigger.BEFORE_LLM_CALL strategies to build the context for a model call."""
        messages = list(raw_messages)
        for strategy in self.compaction_strategies:
            if strategy.trigger == Trigger.BEFORE_LLM_CALL:
                messages = await strategy.apply(
                    messages=messages,
                    session_id=session_id,
                    history=self.history,
                    model_client=model_client,
                )
        return messages

    async def compact(
        self,
        session_id: str,
        trigger: Trigger,
        model_client: Optional["BaseModelClient"] = None,
    ) -> None:
        """Run all strategies registered for a specific trigger to permanently compact history in backing memory."""
        strategies = [s for s in self.compaction_strategies if s.trigger == trigger]
        if not strategies:
            return

        raw_messages = await self.history.load_messages(session_id)
        messages = list(raw_messages)
        original_len = len(messages)

        for strategy in strategies:
            messages = await strategy.apply(
                messages=messages,
                session_id=session_id,
                history=self.history,
                model_client=model_client,
            )

        if len(messages) != original_len or messages != raw_messages:
            await self.history.clear_session(session_id)
            await self.history.save_messages(session_id, messages)

    def __repr__(self) -> str:
        return f"<ModelContext(history={self.history!r}, strategies={self.compaction_strategies})>"


__all__ = ["ModelContext", "CompactionStrategy", "Trigger"]
