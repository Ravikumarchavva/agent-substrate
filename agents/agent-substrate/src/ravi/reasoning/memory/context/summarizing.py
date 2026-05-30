from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from ravi.reasoning.memory.context._helpers import estimate_tokens, split_system
from ravi.fabric.agents_base.compaction import CompactionStrategy, Trigger
from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.kernel.messages.client_messages import SystemMessage
from ravi.logger import setup_logging

if TYPE_CHECKING:
    from ravi.kernel.memory.history_provider import HistoryProvider
    from ravi.kernel.llm.base_client import BaseModelClient

_logger = setup_logging()

_DEFAULT_SUMMARY_SYSTEM: str = (
    "You are a conversation summarizer. Produce a concise but complete "
    "summary preserving key facts, decisions, tool results, and context."
)


class SummarizingStrategy(CompactionStrategy):
    """Summarise older history into a compact system message when threshold is hit."""

    trigger = Trigger.BEFORE_LLM_CALL

    def __init__(
        self,
        summary_client: "BaseModelClient",
        *,
        threshold: float = 0.9,
        model_max_tokens: int = 128_000,
        keep_recent: int = 10,
        summary_system: Optional[str] = None,
    ) -> None:
        if not 0 < threshold < 1:
            raise ValueError("threshold must be between 0 and 1 exclusive")
        if keep_recent < 1:
            raise ValueError("keep_recent must be >= 1")
        self._summary_client = summary_client
        self.threshold = threshold
        self.model_max_tokens = model_max_tokens
        self.keep_recent = keep_recent
        self._summary_system = summary_system or _DEFAULT_SUMMARY_SYSTEM

    async def _summarize(self, messages: List[BaseClientMessage]) -> str:
        from ravi.kernel.messages.client_messages import UserMessage

        summary_request: List[BaseClientMessage] = [
            *messages,
            UserMessage(content=["Summarize the conversation above."]),
        ]
        try:
            response = await self._summary_client.generate_text(
                summary_request,
                system_instructions=self._summary_system,
            )
            if response.content:
                text_parts = [
                    p if isinstance(p, str) else (p.get("text", "") if isinstance(p, dict) else "")
                    for p in response.content
                ]
                return " ".join(t for t in text_parts if t).strip()
        except Exception:
            _logger.exception("SummarizingStrategy summary call failed; falling back")
        return "[Summary unavailable - full history may be truncated]"

    async def apply(
        self,
        messages: List[BaseClientMessage],
        session_id: str,
        history: "HistoryProvider",
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = split_system(messages)
        token_budget = int(self.model_max_tokens * self.threshold)

        async def count_fn(msgs: List[BaseClientMessage]) -> int:
            if model_client is not None and hasattr(model_client, "count_tokens"):
                return await model_client.count_tokens(msgs)  # type: ignore[attr-defined]
            return estimate_tokens(msgs)

        if await count_fn(rest) <= token_budget:
            return messages

        if len(rest) <= self.keep_recent:
            return messages

        to_summarize = rest[: -self.keep_recent]
        keep = rest[-self.keep_recent:]

        summary_text = await self._summarize(to_summarize)
        summary_msg = SystemMessage(
            content=f"[Conversation summary - earlier messages compressed]\n{summary_text}"
        )

        await history.clear_session(session_id)
        rebuilt: List[BaseClientMessage] = []
        if system_msg is not None:
            rebuilt.append(system_msg)
        rebuilt.append(summary_msg)
        for msg in keep:
            rebuilt.append(msg)
        await history.save_messages(session_id, rebuilt)

        return rebuilt

    def __repr__(self) -> str:
        return (
            f"<SummarizingStrategy(threshold={self.threshold}, "
            f"model_max_tokens={self.model_max_tokens}, keep_recent={self.keep_recent})>"
        )
