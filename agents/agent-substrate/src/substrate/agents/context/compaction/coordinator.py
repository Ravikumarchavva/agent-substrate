"""Compaction coordinator executing phase-appropriate context compaction strategies."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from substrate.kernel.agent.context import (
    CompactionContext,
    CompactionPhase,
    CompactionResult,
    CompactionStrategy,
)
from substrate.kernel.core.content import ChatMessage, ContentBlock, Role
from substrate.kernel.exceptions import BudgetExhaustedError

logger = logging.getLogger(__name__)

_DEFAULT_CHARS_PER_TOKEN = 4.0


@runtime_checkable
class CompactionCoordinator(Protocol):
    """Contract for phase-aware context compaction orchestration."""

    async def compact(
        self,
        phase: CompactionPhase,
        context: CompactionContext,
    ) -> CompactionResult:
        ...


def _estimate_message_tokens(msg: ChatMessage, cpt: float = _DEFAULT_CHARS_PER_TOKEN) -> int:
    chars = len(msg.role) + len(msg.text)
    for block in msg.content:
        chars += len(str(block))
    return max(1, int(chars / cpt))


def _estimate_total_tokens(messages: Sequence[ChatMessage], cpt: float = _DEFAULT_CHARS_PER_TOKEN) -> int:
    return sum(_estimate_message_tokens(m, cpt) for m in messages)


class DefaultCompactionCoordinator:
    """Reference implementation for phase-aware compaction orchestration.

    Orchestrates compaction across execution phases with strictly specified failure policies:
    - PRE_LLM:
        Attempts pre-LLM strategy; on failure or absence, falls back to deterministic sliding-window
        turn dropping (preserving system prompt at index 0). If still overflowing budget, raises
        BudgetExhaustedError.
    - POST_TOOL:
        Attempts tool result compaction; on failure, logs warning and safely retains original tool output.
    - POST_TURN:
        Attempts checkpoint generation; on failure, logs warning and discards checkpoint proposal
        without blocking turn completion.
    """

    def __init__(
        self,
        *,
        pre_llm_strategy: CompactionStrategy | None = None,
        tool_strategy: Any | None = None,
        summarizer: Any | None = None,
        chars_per_token: float = _DEFAULT_CHARS_PER_TOKEN,
    ) -> None:
        self._pre_llm_strategy = pre_llm_strategy
        self._tool_strategy = tool_strategy
        self._summarizer = summarizer
        self._cpt = chars_per_token

    async def compact(
        self,
        phase: CompactionPhase,
        context: CompactionContext,
    ) -> CompactionResult:
        if phase == CompactionPhase.PRE_LLM:
            return await self._compact_pre_llm(context)
        elif phase == CompactionPhase.POST_TOOL:
            return await self._compact_post_tool(context)
        elif phase == CompactionPhase.POST_TURN:
            return await self._compact_post_turn(context)
        else:
            raise ValueError(f"Unknown compaction phase: {phase}")

    async def _compact_pre_llm(self, context: CompactionContext) -> CompactionResult:
        messages = list(context.messages)
        if self._pre_llm_strategy is not None:
            try:
                messages = await self._pre_llm_strategy.compact(messages)
            except Exception as exc:
                logger.warning(
                    "Pre-LLM compaction strategy failed (%s); falling back to deterministic truncation",
                    exc,
                )
                messages = self._deterministic_fallback(context.messages, context.token_budget)
        else:
            messages = self._deterministic_fallback(messages, context.token_budget)

        # Budget verification
        if context.token_budget is not None:
            current_tokens = _estimate_total_tokens(messages, self._cpt)
            if current_tokens > context.token_budget:
                messages = self._deterministic_fallback(messages, context.token_budget)

        return CompactionResult(
            phase=CompactionPhase.PRE_LLM,
            prompt_messages=messages,
        )

    def _deterministic_fallback(
        self,
        messages: Sequence[ChatMessage],
        token_budget: int | None,
    ) -> list[ChatMessage]:
        if token_budget is None or not messages:
            return list(messages)

        current_tokens = _estimate_total_tokens(messages, self._cpt)
        if current_tokens <= token_budget:
            return list(messages)

        working = list(messages)
        system_msg: ChatMessage | None = None
        if working and working[0].role == Role.SYSTEM:
            system_msg = working.pop(0)

        prefix = [system_msg] if system_msg else []
        while working and _estimate_total_tokens(prefix + working, self._cpt) > token_budget:
            working.pop(0)
            while working and working[0].role == Role.TOOL:
                working.pop(0)

        result = prefix + working
        if _estimate_total_tokens(result, self._cpt) > token_budget:
            raise BudgetExhaustedError(
                f"Context exceeds token budget ({_estimate_total_tokens(result, self._cpt)} > {token_budget}) "
                "even after dropping all non-system turns"
            )

        return result

    async def _compact_post_tool(self, context: CompactionContext) -> CompactionResult:
        if context.tool_content is None:
            raise ValueError("tool_content must be provided in CompactionContext for POST_TOOL phase")

        compacted: ContentBlock = context.tool_content
        if self._tool_strategy is not None:
            try:
                if hasattr(self._tool_strategy, "compact_block"):
                    compacted = await self._tool_strategy.compact_block(context.tool_content)
                elif hasattr(self._tool_strategy, "compact"):
                    compacted = await self._tool_strategy.compact(context.tool_content)
            except Exception as exc:
                logger.warning(
                    "Post-tool compaction strategy failed (%s); retaining original tool output",
                    exc,
                )
                compacted = context.tool_content

        return CompactionResult(
            phase=CompactionPhase.POST_TOOL,
            compacted_content=compacted,
        )

    async def _compact_post_turn(self, context: CompactionContext) -> CompactionResult:
        proposal = None
        if self._summarizer is not None:
            try:
                if hasattr(self._summarizer, "create_checkpoint"):
                    proposal = await self._summarizer.create_checkpoint(context)
                elif hasattr(self._summarizer, "summarize"):
                    proposal = await self._summarizer.summarize(context)
            except Exception as exc:
                logger.warning(
                    "Post-turn checkpoint generation failed (%s); discarding proposal to avoid blocking turn",
                    exc,
                )
                proposal = None

        return CompactionResult(
            phase=CompactionPhase.POST_TURN,
            checkpoint_proposal=proposal,
        )


__all__ = ["CompactionCoordinator", "DefaultCompactionCoordinator"]

