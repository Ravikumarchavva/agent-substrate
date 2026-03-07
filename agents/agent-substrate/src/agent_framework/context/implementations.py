"""Concrete ModelContext implementations.

Four built-in strategies ship out of the box:

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
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, List, Optional

from agent_framework.context.base_context import ModelContext
from agent_framework.messages.base_message import BaseClientMessage
from agent_framework.messages.client_messages import SystemMessage

if TYPE_CHECKING:
    from agent_framework.memory.session_manager import SessionManager
    from agent_framework.model_clients.base_client import BaseModelClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(messages: List[BaseClientMessage]) -> int:
    """Very rough token estimate: 4 chars ≈ 1 token."""
    total = 0
    for msg in messages:
        total += len(json.dumps(msg.model_dump() if hasattr(msg, "model_dump") else str(msg))) // 4
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
                    json.dumps(m.model_dump() if hasattr(m, "model_dump") else str(m), sort_keys=True)
                    for m in combined
                }
                unique_cold: List[BaseClientMessage] = []
                for m in cold_messages:
                    if id(m) in seen:
                        continue
                    s = json.dumps(m.model_dump() if hasattr(m, "model_dump") else str(m), sort_keys=True)
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
        return (
            f"<HybridContext(recent_n={self.recent_n}, max_total={self.max_total})>"
        )
