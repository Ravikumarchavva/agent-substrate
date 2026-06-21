"""SummarizationCompaction — condenses old turns into an LLM-generated summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.kernel.core.content import ChatMessage, TextBlock
from substrate.kernel.llm import GenerationOptions
from substrate.logger import setup_logging

if TYPE_CHECKING:
    from substrate.kernel.llm import LLMClient

logger = setup_logging()

_SYSTEM_PROMPT = (
    "You are a conversation summarizer. "
    "Produce a concise factual summary of the conversation below. "
    "Capture: key decisions, facts established, user intent, and any outstanding tasks. "
    "Write in third-person past tense. Be brief — aim for 3-7 sentences."
)

_UPDATE_SYSTEM_PROMPT = (
    "You are a conversation summarizer. "
    "You will be given an existing summary and new conversation messages. "
    "Produce an updated summary that integrates the new information. "
    "Preserve all key decisions, facts, user intent, and outstanding tasks. "
    "Write in third-person past tense. Be brief — aim for 3-7 sentences."
)

_SUMMARY_PREFIX = "[Earlier conversation summary]"

_DEFAULT_CPT = 4.0


class SummarizationCompaction:
    """Summarizes old messages with an LLM, keeping recent turns verbatim.

    Aggressiveness: Medium
    Preserves context: Medium — replaces history with a structured summary.
    Requires LLM: Yes

    Split is token-based so the strategy adapts to models with different context
    windows and to conversations where message sizes vary widely.

    Args:
        model:               Any LLMClient — a cheap/fast model is sufficient.
        recent_token_budget: Tokens to keep verbatim in the recent window.
        min_old_tokens:      Skip compaction when the old slice is smaller than this.
        chars_per_token:     Estimation ratio. Default 4.0 for English text.
    """

    def __init__(
        self,
        model: LLMClient,
        recent_token_budget: int = 32_000,
        min_old_tokens: int = 1_000,
        chars_per_token: float = _DEFAULT_CPT,
    ) -> None:
        self._model = model
        self._recent_token_budget = recent_token_budget
        self._min_old_tokens = min_old_tokens
        self._cpt = chars_per_token

    async def compact(self, raw_history: list[ChatMessage]) -> list[ChatMessage]:
        old, recent = self._split(raw_history)

        if _estimate_tokens_list(old, self._cpt) < self._min_old_tokens:
            return raw_history

        leading_summary: str | None = None
        substantive_old = old
        if old and _is_summary(old[0]):
            leading_summary = _extract_summary_text(old[0])
            substantive_old = old[1:]

        summary = await self._get_summary(
            substantive_old, existing_summary=leading_summary
        )
        return [_make_summary_message(summary)] + recent

    def _split(
        self, history: list[ChatMessage]
    ) -> tuple[list[ChatMessage], list[ChatMessage]]:
        recent: list[ChatMessage] = []
        tokens = 0
        for msg in reversed(history):
            t = _estimate_tokens(msg, self._cpt)
            if tokens + t > self._recent_token_budget:
                break
            recent.insert(0, msg)
            tokens += t
        old = history[: len(history) - len(recent)]
        return old, recent

    async def _get_summary(
        self,
        messages: list[ChatMessage],
        *,
        existing_summary: str | None,
    ) -> str:
        if existing_summary:
            text = _messages_to_text(messages)
            prompt_text = (
                f"Existing summary:\n{existing_summary}\n\n"
                f"New messages to incorporate:\n{text}"
            )
            system = _UPDATE_SYSTEM_PROMPT
        else:
            text = _messages_to_text(messages)
            prompt_text = f"Conversation to summarize:\n\n{text}"
            system = _SYSTEM_PROMPT

        prompt = ChatMessage(role="user", content=[TextBlock(text=prompt_text)])
        try:
            resp = await self._model.generate(
                [prompt],
                options=GenerationOptions(system_instructions=system),
            )
            summary = " ".join(
                b.text for b in resp.content if isinstance(b, TextBlock) and b.text
            ).strip()
        except Exception as exc:
            logger.warning(
                "SummarizationCompaction: LLM call failed (%s); using placeholder", exc
            )
            summary = (
                f"[Summary unavailable — {len(messages)} earlier messages omitted]"
            )

        return summary


def _estimate_tokens(msg: ChatMessage, cpt: float) -> int:
    chars = sum(len(b.text) for b in msg.content if isinstance(b, TextBlock))
    return max(1, int(chars / cpt))


def _estimate_tokens_list(messages: list[ChatMessage], cpt: float) -> int:
    return sum(_estimate_tokens(m, cpt) for m in messages)


def _is_summary(msg: ChatMessage) -> bool:
    if msg.role != "system":
        return False
    return any(
        isinstance(b, TextBlock) and b.text.startswith(_SUMMARY_PREFIX)
        for b in msg.content
    )


def _extract_summary_text(msg: ChatMessage) -> str:
    for b in msg.content:
        if isinstance(b, TextBlock) and b.text.startswith(_SUMMARY_PREFIX):
            return b.text[len(_SUMMARY_PREFIX) :].strip()
    return ""


def _messages_to_text(messages: list[ChatMessage]) -> str:
    lines: list[str] = []
    for msg in messages:
        if msg.role == "system":
            for b in msg.content:
                if isinstance(b, TextBlock) and b.text.startswith(_SUMMARY_PREFIX):
                    lines.append(f"SUMMARY: {b.text[len(_SUMMARY_PREFIX) :].strip()}")
            continue
        text = " ".join(
            b.text for b in msg.content if isinstance(b, TextBlock) and b.text
        )
        if text:
            lines.append(f"{msg.role.upper()}: {text}")
    return "\n".join(lines)


def _make_summary_message(summary: str) -> ChatMessage:
    return ChatMessage(
        role="system",
        content=[TextBlock(text=f"{_SUMMARY_PREFIX}\n{summary}")],
    )


__all__ = ["SummarizationCompaction"]
