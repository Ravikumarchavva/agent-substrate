"""Tests for CompactionPhase, CompactionResult, and DefaultCompactionCoordinator."""

import pytest
from pydantic import ValidationError

from substrate.agents.context.compaction.coordinator import DefaultCompactionCoordinator
from substrate.kernel.agent.context import (
    CompactionContext,
    CompactionPhase,
    CompactionResult,
)
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.exceptions import BudgetExhaustedError
from substrate.kernel.storage.history import HistoryCheckpoint


def _msg(text: str, role: Role = Role.USER) -> ChatMessage:
    return ChatMessage(role=role, content=[TextBlock(text=text)])


class TestCompactionResultValidation:
    def test_pre_llm_validation(self) -> None:
        # Valid PRE_LLM
        res = CompactionResult(
            phase=CompactionPhase.PRE_LLM,
            prompt_messages=[_msg("hi")],
        )
        assert res.phase == CompactionPhase.PRE_LLM
        assert len(res.prompt_messages) == 1

        # Missing prompt_messages
        with pytest.raises(ValidationError, match="prompt_messages is required"):
            CompactionResult(phase=CompactionPhase.PRE_LLM)

        # Illegal field compacted_content
        with pytest.raises(ValidationError, match="Only prompt_messages may be set"):
            CompactionResult(
                phase=CompactionPhase.PRE_LLM,
                prompt_messages=[_msg("hi")],
                compacted_content=TextBlock(text="illegal"),
            )

    def test_post_tool_validation(self) -> None:
        # Valid POST_TOOL
        res = CompactionResult(
            phase=CompactionPhase.POST_TOOL,
            compacted_content=TextBlock(text="tool output"),
        )
        assert res.phase == CompactionPhase.POST_TOOL
        assert res.compacted_content.text == "tool output"

        # Missing compacted_content
        with pytest.raises(ValidationError, match="compacted_content is required"):
            CompactionResult(phase=CompactionPhase.POST_TOOL)

        # Illegal field prompt_messages
        with pytest.raises(ValidationError, match="Only compacted_content may be set"):
            CompactionResult(
                phase=CompactionPhase.POST_TOOL,
                compacted_content=TextBlock(text="ok"),
                prompt_messages=[_msg("illegal")],
            )

    def test_post_turn_validation(self) -> None:
        # Valid POST_TURN with checkpoint
        cp = HistoryCheckpoint(
            session_id="sess-1",
            anchor_message_id="msg-1",
            summary="sum",
        )
        res = CompactionResult(
            phase=CompactionPhase.POST_TURN,
            checkpoint_proposal=cp,
        )
        assert res.checkpoint_proposal == cp

        # Valid POST_TURN with None checkpoint
        res_none = CompactionResult(
            phase=CompactionPhase.POST_TURN,
            checkpoint_proposal=None,
        )
        assert res_none.checkpoint_proposal is None

        # Illegal field prompt_messages
        with pytest.raises(ValidationError, match="must be None for POST_TURN"):
            CompactionResult(
                phase=CompactionPhase.POST_TURN,
                prompt_messages=[_msg("illegal")],
            )


class TestDefaultCompactionCoordinator:
    @pytest.mark.asyncio
    async def test_pre_llm_deterministic_fallback_on_strategy_failure(self) -> None:
        class FailingStrategy:
            async def compact(self, messages: list[ChatMessage]) -> list[ChatMessage]:
                raise RuntimeError("LLM API failed")

        coordinator = DefaultCompactionCoordinator(pre_llm_strategy=FailingStrategy())

        sys_msg = _msg("system instruction", Role.SYSTEM)
        msg1 = _msg("old message 1")
        msg2 = _msg("old message 2")
        msg3 = _msg("recent message 3")

        ctx = CompactionContext(
            session_id="s1",
            messages=[sys_msg, msg1, msg2, msg3],
            token_budget=15,  # Enough for system + msg3, but not all
        )

        res = await coordinator.compact(CompactionPhase.PRE_LLM, ctx)
        assert res.phase == CompactionPhase.PRE_LLM
        assert res.prompt_messages is not None
        # System prompt preserved at index 0
        assert res.prompt_messages[0].role == Role.SYSTEM
        # Old messages dropped
        assert len(res.prompt_messages) < 4

    @pytest.mark.asyncio
    async def test_pre_llm_raises_budget_exhausted_when_system_exceeds_budget(self) -> None:
        coordinator = DefaultCompactionCoordinator()
        sys_msg = _msg("a very very very long system prompt that exceeds budget", Role.SYSTEM)
        ctx = CompactionContext(
            session_id="s1",
            messages=[sys_msg, _msg("hello")],
            token_budget=2,  # Far too small
        )

        with pytest.raises(BudgetExhaustedError, match="exceeds token budget"):
            await coordinator.compact(CompactionPhase.PRE_LLM, ctx)

    @pytest.mark.asyncio
    async def test_post_tool_failure_retains_original_content(self) -> None:
        class FailingToolStrategy:
            async def compact_block(self, content: TextBlock) -> TextBlock:
                raise RuntimeError("Compactor error")

        coordinator = DefaultCompactionCoordinator(tool_strategy=FailingToolStrategy())
        original_block = TextBlock(text="raw output")

        ctx = CompactionContext(
            session_id="s1",
            tool_content=original_block,
        )

        res = await coordinator.compact(CompactionPhase.POST_TOOL, ctx)
        assert res.phase == CompactionPhase.POST_TOOL
        assert res.compacted_content == original_block

    @pytest.mark.asyncio
    async def test_post_tool_missing_content_raises(self) -> None:
        coordinator = DefaultCompactionCoordinator()
        ctx = CompactionContext(session_id="s1")

        with pytest.raises(ValueError, match="tool_content must be provided"):
            await coordinator.compact(CompactionPhase.POST_TOOL, ctx)

    @pytest.mark.asyncio
    async def test_post_turn_failure_discards_checkpoint(self) -> None:
        class FailingSummarizer:
            async def create_checkpoint(self, ctx: CompactionContext) -> HistoryCheckpoint:
                raise RuntimeError("LLM summarizer down")

        coordinator = DefaultCompactionCoordinator(summarizer=FailingSummarizer())
        ctx = CompactionContext(
            session_id="s1",
            leaf_node_id="n1",
        )

        res = await coordinator.compact(CompactionPhase.POST_TURN, ctx)
        assert res.phase == CompactionPhase.POST_TURN
        assert res.checkpoint_proposal is None

