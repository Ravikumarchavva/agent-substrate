"""Concrete ModelContext implementations.

Six built-in strategies ship out of the box:

UnboundedContext
    Returns all messages unchanged.  Zero-overhead default — identical
    behaviour to the pre-ModelContext agent loop.

SlidingWindowContext
    Always preserves the SystemMessage then keeps the last ``max_messages``
    messages.  Fast and deterministic; good for long-running conversations
    where older history is rarely relevant.

TokenBudgetContext
    Walks messages from oldest to newest and drops them until the estimated
    token count falls within ``max_tokens``.  The SystemMessage is always
    preserved regardless of budget.  Uses a simple character-based estimator
    (4 chars ≈ 1 token) unless the model client exposes a ``count_tokens``
    method.

HybridContext
    Fuses Redis (hot / recent) memory with Postgres (cold / long-term) memory
    via a SessionManager.  Retrieves the most recent ``recent_n`` messages
    from Redis, then back-fills with older Postgres messages until
    ``max_total`` messages are reached.  Deduplicates by message identity.

RedisModelContext
    Reads *directly* from a ``RedisMemory`` instance — completely ignoring
    the in-process ``raw_messages`` argument.  Used together with
    ``RedisMemory`` to build a truly stateless agent where the only
    persistent state is the ``session_id``.

SummarizingContext
    LLM-based compression.  When the non-system history crosses
    ``threshold × model_max_tokens`` tokens, the older portion is summarised
    by a separate LLM call and the backing ``Memory`` is rewritten in-place
    so future turns start from the compressed state.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, List, Optional

from agent_framework.core.context.base_context import ModelContext
from agent_framework.core.messages.base_message import BaseClientMessage
from agent_framework.core.messages.client_messages import SystemMessage

if TYPE_CHECKING:
    from agent_framework.core.memory.base_memory import BaseMemory
    from agent_framework.core.memory.redis_memory import RedisMemory
    from agent_framework.core.memory.session_manager import SessionManager
    from agent_framework.providers.llm.base_client import BaseModelClient

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(messages: List[BaseClientMessage]) -> int:
    """Very rough token estimate: 4 chars ≈ 1 token."""
    total = 0
    for msg in messages:
        total += (
            len(
                json.dumps(msg.model_dump() if hasattr(msg, "model_dump") else str(msg))
            )
            // 4
        )
    return total


def _split_system(
    messages: List[BaseClientMessage],
) -> tuple[Optional[BaseClientMessage], List[BaseClientMessage]]:
    """Separate the first SystemMessage from the rest."""
    if messages and isinstance(messages[0], SystemMessage):
        return messages[0], messages[1:]
    return None, messages


# ---------------------------------------------------------------------------
# UnboundedContext
# ---------------------------------------------------------------------------


class UnboundedContext(ModelContext):
    """Pass-through — returns all messages unchanged.

    This is the *default* context used by ``ReActAgent`` when no
    ``model_context`` is provided.  It preserves the previous behaviour
    exactly so existing code is unaffected.
    """

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        return raw_messages

    def __repr__(self) -> str:
        return "<UnboundedContext>"


# ---------------------------------------------------------------------------
# SlidingWindowContext
# ---------------------------------------------------------------------------


class SlidingWindowContext(ModelContext):
    """Keep the system prompt + the last *max_messages* conversation turns.

    Args:
        max_messages: Maximum number of non-system messages to retain.
            Defaults to 20 (≈ 10 back-and-forth exchanges).
    """

    def __init__(self, max_messages: int = 20) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be ≥ 1")
        self.max_messages = max_messages

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = _split_system(raw_messages)
        windowed = rest[-self.max_messages :] if len(rest) > self.max_messages else rest
        if system_msg is not None:
            return [system_msg, *windowed]
        return windowed

    def __repr__(self) -> str:
        return f"<SlidingWindowContext(max_messages={self.max_messages})>"


# ---------------------------------------------------------------------------
# TokenBudgetContext
# ---------------------------------------------------------------------------


class TokenBudgetContext(ModelContext):
    """Trim oldest messages until the history fits within *max_tokens*.

    The SystemMessage is **always** preserved — it is never counted against the
    budget.  Messages are removed from the oldest (non-system) end until the
    estimated token count is within budget.

    Args:
        max_tokens: Hard upper bound on the total estimated token count for
            the message list passed to the model.  Defaults to 8 000.
    """

    def __init__(self, max_tokens: int = 8_000) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be ≥ 1")
        self.max_tokens = max_tokens

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = _split_system(raw_messages)

        # Use model client's token counter if available, else char-based estimate
        def token_count(msgs: List[BaseClientMessage]) -> int:
            if model_client is not None and hasattr(model_client, "count_tokens"):
                return model_client.count_tokens(msgs)  # type: ignore[attr-defined]
            return _estimate_tokens(msgs)

        # Drop oldest messages until within budget
        trimmed = list(rest)
        while trimmed and token_count(trimmed) > self.max_tokens:
            trimmed.pop(0)

        if system_msg is not None:
            return [system_msg, *trimmed]
        return trimmed

    def __repr__(self) -> str:
        return f"<TokenBudgetContext(max_tokens={self.max_tokens})>"


# ---------------------------------------------------------------------------
# HybridContext
# ---------------------------------------------------------------------------


class HybridContext(ModelContext):
    """Fuse Redis (hot) and Postgres (cold) memory via a ``SessionManager``.

    Strategy:
    1. Fetch the ``recent_n`` latest messages from Redis (fast path).
    2. If the total is still below ``max_total``, back-fill with older
       messages fetched from Postgres.
    3. Deduplicate by object identity / serialised content so messages that
       appear in both tiers are not repeated.
    4. Prepend the SystemMessage if present.

    This context is ideal for long-running or restored sessions where Redis
    may not hold the full history but recent context matters most.

    Args:
        session_manager: A connected ``SessionManager`` instance.
        recent_n:   How many recent messages to pull from Redis.  Defaults to 20.
        max_total:  Maximum total messages in the final list (excluding system
                    prompt).  Defaults to 40.
    """

    def __init__(
        self,
        session_manager: "SessionManager",
        recent_n: int = 20,
        max_total: int = 40,
    ) -> None:
        if recent_n < 1 or max_total < 1:
            raise ValueError("recent_n and max_total must be ≥ 1")
        if recent_n > max_total:
            raise ValueError("recent_n cannot exceed max_total")
        self._session_manager = session_manager
        self.recent_n = recent_n
        self.max_total = max_total

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        # Separate out the system message from raw (in-process) messages
        system_msg, rest = _split_system(raw_messages)

        # --- Hot tier: most recent messages from in-process memory -----------
        recent = rest[-self.recent_n :] if len(rest) > self.recent_n else list(rest)

        # --- Cold tier: back-fill from Postgres if needed -------------------
        combined = recent
        if len(combined) < self.max_total:
            try:
                needed = self.max_total - len(combined)
                cold_messages = await self._session_manager.load_messages(
                    session_id=session_id,
                    limit=needed + self.recent_n,  # fetch extra for dedup
                )
                # Deduplicate: keep cold messages not already in recent
                seen = {id(m) for m in combined}
                # Serialise recent for content-based dedup fallback
                seen_serialised = {
                    json.dumps(
                        m.model_dump() if hasattr(m, "model_dump") else str(m),
                        sort_keys=True,
                    )
                    for m in combined
                }
                unique_cold: List[BaseClientMessage] = []
                for m in cold_messages:
                    if id(m) in seen:
                        continue
                    s = json.dumps(
                        m.model_dump() if hasattr(m, "model_dump") else str(m),
                        sort_keys=True,
                    )
                    if s in seen_serialised:
                        continue
                    seen.add(id(m))
                    seen_serialised.add(s)
                    unique_cold.append(m)

                # Prepend cold (older) messages before recent
                combined = unique_cold[:needed] + combined
            except Exception:
                # If cold retrieval fails, degrade gracefully to hot-only
                pass

        if system_msg is not None:
            return [system_msg, *combined]
        return combined

    def __repr__(self) -> str:
        return f"<HybridContext(recent_n={self.recent_n}, max_total={self.max_total})>"


# ---------------------------------------------------------------------------
# RedisModelContext
# ---------------------------------------------------------------------------


class RedisModelContext(ModelContext):
    """Stateless context that reads directly from a ``RedisMemory`` instance.

    Unlike every other ``ModelContext`` implementation, this one **ignores**
    the ``raw_messages`` argument (which comes from the agent's in-process
    memory).  Instead it reads the local cache of the attached ``RedisMemory``
    — which is always in sync with Redis because every ``add_message()`` call
    schedules a Redis write.

    This makes the agent truly stateless: the only information required to
    recreate an equivalent agent is the ``session_id``.  Recreate the
    ``RedisMemory`` with the same ``session_id``, call ``restore()``,
    pass it to a new ``RedisModelContext``, and the new agent picks up from
    exactly the right context window.

    Args:
        redis_memory: A ``RedisMemory`` instance (already connected and
            optionally restored, constructed with a ``session_id``).
        recent_n:    Number of most-recent messages to pass to the LLM (not
            counting the system prompt).  Defaults to 10.
    """

    def __init__(
        self,
        redis_memory: "RedisMemory",
        recent_n: int = 10,
    ) -> None:
        if recent_n < 1:
            raise ValueError("recent_n must be ≥ 1")
        self._redis_memory = redis_memory
        self.recent_n = recent_n

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        # Read from RedisMemory's local cache (synced with Redis),
        # completely ignoring the in-process raw_messages argument.
        all_messages = await self._redis_memory.get_messages()

        system_msg, rest = _split_system(all_messages)

        # Keep the most recent `recent_n` non-system messages
        windowed = rest[-self.recent_n :] if len(rest) > self.recent_n else list(rest)

        if system_msg is not None:
            return [system_msg, *windowed]
        return windowed

    def __repr__(self) -> str:
        return (
            f"<RedisModelContext(session_id={self._redis_memory.session_id!r}, "
            f"recent_n={self.recent_n})>"
        )


# ---------------------------------------------------------------------------
# SummarizingContext
# ---------------------------------------------------------------------------


class SummarizingContext(ModelContext):
    """Compress history by LLM-summarising old messages when token threshold is hit.

    On each call to ``build()``:

    1. Estimate total token count of the non-system message history.
    2. If below ``threshold × model_max_tokens``: return messages unchanged.
    3. When over threshold:

       a. Keep the last ``keep_recent`` messages verbatim.
       b. Call the summary LLM on the older portion.
       c. **Rewrite backing memory** — clear and re-seed with
          ``[system_msg, summary_msg, keep_recent messages]``.
       d. Return the rewritten list so the LLM call fits within budget.

    The memory rewrite in step (c) means future turns start from the
    compressed state — summarisation only fires when genuinely needed,
    not on every turn.

    Args:
        memory:           Agent's ``BaseMemory`` instance.  Mutated in-place when
                          summarisation fires to keep the backing store compact.
        summary_client:   ``BaseModelClient`` used to generate the summary.
                          May be the same client the agent uses.
        threshold:        Fraction of ``model_max_tokens`` at which to trigger.
                          Defaults to ``0.9``.
        model_max_tokens: Token limit of the target model.  Defaults to 128 000.
        keep_recent:      Non-system messages kept verbatim after compression.
                          Defaults to 10.
        summary_system:   Override system prompt for the summary LLM call.
    """

    _DEFAULT_SUMMARY_SYSTEM: str = (
        "You are a conversation summarizer.  Produce a concise but complete "
        "summary of the conversation below, preserving all key facts, "
        "decisions, tool results, and context needed to continue the "
        "conversation coherently.  Write in third person.  Do not include "
        "greetings or meta-commentary."
    )

    def __init__(
        self,
        memory: "BaseMemory",
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
            raise ValueError("keep_recent must be \u2265 1")
        self._memory = memory
        self._summary_client = summary_client
        self.threshold = threshold
        self.model_max_tokens = model_max_tokens
        self.keep_recent = keep_recent
        self._summary_system = summary_system or self._DEFAULT_SUMMARY_SYSTEM

    async def _summarize(self, messages: List[BaseClientMessage]) -> str:
        """Call the summary LLM and return summary text."""
        from agent_framework.core.messages.client_messages import UserMessage

        summary_request: List[BaseClientMessage] = [
            SystemMessage(content=self._summary_system),
            *messages,
            UserMessage(
                content=[{"type": "text", "text": "Summarize the conversation above."}]
            ),
        ]
        try:
            response = await self._summary_client.generate(messages=summary_request)
            if response.content:
                text_parts = [
                    p
                    if isinstance(p, str)
                    else (p.get("text", "") if isinstance(p, dict) else "")
                    for p in response.content
                ]
                return " ".join(t for t in text_parts if t).strip()
        except Exception:
            _logger.exception(
                "SummarizingContext: summary LLM call failed — using fallback"
            )
        return "[Summary unavailable — full history may be truncated]"

    async def build(
        self,
        *,
        session_id: str,
        current_input: str,
        raw_messages: List[BaseClientMessage],
        model_client: Optional["BaseModelClient"] = None,
    ) -> List[BaseClientMessage]:
        system_msg, rest = _split_system(raw_messages)

        token_budget = int(self.model_max_tokens * self.threshold)
        count_fn = (
            model_client.count_tokens  # type: ignore[attr-defined]
            if model_client is not None and hasattr(model_client, "count_tokens")
            else _estimate_tokens
        )

        # Within budget — pass through unchanged
        if count_fn(rest) <= token_budget:
            return raw_messages

        # Not enough history to make summarisation worthwhile
        if len(rest) <= self.keep_recent:
            return raw_messages

        to_summarize = rest[: -self.keep_recent]
        keep = rest[-self.keep_recent :]

        _logger.info(
            "SummarizingContext[%s]: threshold hit — summarising %d messages, keeping %d recent",
            session_id,
            len(to_summarize),
            len(keep),
        )

        summary_text = await self._summarize(to_summarize)
        summary_msg = SystemMessage(
            content=(
                f"[Conversation summary — earlier messages compressed]\n{summary_text}"
            )
        )

        # Rewrite backing memory so the next turn starts from compressed state
        await self._memory.clear()
        rebuilt: List[BaseClientMessage] = []
        if system_msg is not None:
            rebuilt.append(system_msg)
            await self._memory.add_message(system_msg)
        rebuilt.append(summary_msg)
        await self._memory.add_message(summary_msg)
        for m in keep:
            rebuilt.append(m)
            await self._memory.add_message(m)

        return rebuilt

    def __repr__(self) -> str:
        return (
            f"<SummarizingContext(threshold={self.threshold}, "
            f"model_max_tokens={self.model_max_tokens}, "
            f"keep_recent={self.keep_recent})>"
        )
