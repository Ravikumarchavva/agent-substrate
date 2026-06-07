"""SummarizationStrategy — condenses old turns into an LLM-generated summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ravi.kernel import Message
from ravi.kernel.content import ChatMessage, TextBlock
from ravi.kernel.identity import AgentId
from ravi.logger import setup_logging

if TYPE_CHECKING:
    from ravi.kernel.llm import LLMClient

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
_SENTINEL = AgentId("compaction", "summary")

# Characters per token — approximate for English text. Used for estimation only.
_DEFAULT_CPT = 4.0


class SummarizationStrategy:
    """Summarizes old messages with an LLM, keeping recent turns verbatim.

    Aggressiveness: Medium
    Preserves context: Medium — replaces history with a structured summary.
    Requires LLM: Yes

    Split is **token-based**, not message-count-based, so the strategy adapts
    automatically to models with different context windows and to conversations
    where message sizes vary widely (e.g. large tool results).

    When history exceeds ``recent_token_budget + min_old_tokens`` tokens:
      - Messages are kept verbatim from newest backwards until
        ``recent_token_budget`` tokens are accumulated ("recent" window).
      - Remaining older messages form the "old" slice.
      - If a previous summary already exists at the front of history, it is
        incorporated into the new summary (incremental update via a second
        LLM prompt) — so information is never lost across compaction rounds.
      - On LLM failure the strategy degrades gracefully and never raises.

    Token estimation uses character counting (``chars_per_token`` ratio,
    default 4.0). Exact token counting is not required for a trigger heuristic.

    Args:
        model:               Any ``LLMClient`` — a cheap/fast model is sufficient.
        recent_token_budget: Tokens to keep verbatim in the recent window.
                             Derive from ``context_length * target_ratio``
                             (e.g. ``128_000 * 0.40 = 51_200``).
        min_old_tokens:      Skip compaction when the old slice is smaller than
                             this. Avoids LLM calls for tiny histories.
        chars_per_token:     Estimation ratio. Default 4.0 is reasonable for
                             English; lower for code-heavy conversations.
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

    async def compact(self, raw_history: list[Message]) -> list[Message]:
        old, recent = self._split(raw_history)

        if _estimate_tokens_list(old, self._cpt) < self._min_old_tokens:
            return raw_history

        # Separate any leading summary from the old slice so we can do an
        # incremental update instead of re-summarizing everything from scratch.
        leading_summary: str | None = None
        substantive_old = old
        if old and _is_summary(old[0]):
            leading_summary = _extract_summary_text(old[0])
            substantive_old = old[1:]

        summary = await self._get_summary(substantive_old, existing_summary=leading_summary)
        return [_make_summary_envelope(summary)] + recent

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split(self, history: list[Message]) -> tuple[list[Message], list[Message]]:
        """Fill 'recent' from newest backwards until recent_token_budget is spent."""
        recent: list[Message] = []
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
        messages: list[Message],
        *,
        existing_summary: str | None,
    ) -> str:
        if existing_summary:
            # Incremental update: short prompt = (prev summary) + (new batch)
            # Input is O(summary_size + batch_size) regardless of total history length.
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

        prompt = ChatMessage(
            role="user",
            content=[TextBlock(text=prompt_text)],
        )
        try:
            resp = await self._model.generate([prompt], system=system)
            summary = " ".join(
                b.text for b in resp.content if isinstance(b, TextBlock) and b.text
            ).strip()
        except Exception as exc:
            logger.warning(
                "SummarizationStrategy: LLM call failed (%s); using placeholder", exc
            )
            summary = f"[Summary unavailable — {len(messages)} earlier messages omitted]"

        return summary


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _estimate_tokens(msg: Message, cpt: float) -> int:
    if not isinstance(msg.payload, ChatMessage):
        return 0
    chars = sum(
        len(b.text)
        for b in msg.payload.content
        if isinstance(b, TextBlock)
    )
    return max(1, int(chars / cpt))


def _estimate_tokens_list(messages: list[Message], cpt: float) -> int:
    return sum(_estimate_tokens(m, cpt) for m in messages)


def _is_summary(msg: Message) -> bool:
    if not isinstance(msg.payload, ChatMessage):
        return False
    if msg.payload.role != "system":
        return False
    return any(
        isinstance(b, TextBlock) and b.text.startswith(_SUMMARY_PREFIX)
        for b in msg.payload.content
    )


def _extract_summary_text(msg: Message) -> str:
    """Return the raw summary text without the prefix header."""
    if not isinstance(msg.payload, ChatMessage):
        return ""
    for b in msg.payload.content:
        if isinstance(b, TextBlock) and b.text.startswith(_SUMMARY_PREFIX):
            return b.text[len(_SUMMARY_PREFIX) :].strip()
    return ""


def _messages_to_text(messages: list[Message]) -> str:
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg.payload, ChatMessage):
            continue
        role = msg.payload.role
        if role == "system":
            # Include previous summary text so incremental prompts stay coherent.
            for b in msg.payload.content:
                if isinstance(b, TextBlock) and b.text.startswith(_SUMMARY_PREFIX):
                    lines.append(f"SUMMARY: {b.text[len(_SUMMARY_PREFIX):].strip()}")
            continue
        text = " ".join(
            b.text for b in msg.payload.content if isinstance(b, TextBlock) and b.text
        )
        if text:
            lines.append(f"{role.upper()}: {text}")
    return "\n".join(lines)


def _make_summary_envelope(summary: str) -> Message:
    chat_msg = ChatMessage(
        role="system",
        content=[TextBlock(text=f"{_SUMMARY_PREFIX}\n{summary}")],
    )
    return Message(target=_SENTINEL, payload=chat_msg)


__all__ = ["SummarizationStrategy"]
