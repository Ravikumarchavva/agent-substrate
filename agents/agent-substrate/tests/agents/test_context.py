from __future__ import annotations

import pytest
from agent_substrate.agents.context import (
    AgentContext,
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    SummarizationCompaction,
    TokenBudgetComposedStrategy,
    CompactionPipeline,
)
from agent_substrate.kernel import AgentId
from agent_substrate.kernel.core.content import ChatMessage, TextBlock
from agent_substrate.kernel.llm import GenerationOptions, LLMResponse, Usage


@pytest.mark.asyncio
async def test_sliding_window_compaction():
    strategy = SlidingWindowCompaction(max_messages=2)
    messages = [
        ChatMessage(role="user", content=[TextBlock(text="1")]),
        ChatMessage(role="user", content=[TextBlock(text="2")]),
        ChatMessage(role="user", content=[TextBlock(text="3")]),
    ]
    compacted = await strategy.compact(messages)
    assert len(compacted) == 2
    assert compacted[0].content[0].text == "2"
    assert compacted[1].content[0].text == "3"


@pytest.mark.asyncio
async def test_context_config():
    history = InMemoryHistoryProvider()
    pipeline = CompactionPipeline([SlidingWindowCompaction(max_messages=10)])
    cfg = ContextConfig(history, pipeline)

    assert cfg.history is history
    assert cfg.pipeline is pipeline

    default_cfg = ContextConfig.default()
    assert isinstance(default_cfg.history, InMemoryHistoryProvider)
    assert isinstance(default_cfg.pipeline, CompactionPipeline)
    assert isinstance(default_cfg.pipeline._strategies[0], SlidingWindowCompaction)


@pytest.mark.asyncio
async def test_agent_context():
    history = InMemoryHistoryProvider()
    pipeline = CompactionPipeline([SlidingWindowCompaction(max_messages=10)])
    agent_id = AgentId(type="assistant", key="agent_1")
    session_id = "test-session"

    chat_msg = ChatMessage(role="user", content=[TextBlock(text="hi")])
    await history.append(agent_id, chat_msg, session_id=session_id)

    ctx = AgentContext(agent_id, history, pipeline)
    assert ctx.agent_id == agent_id
    assert ctx.history is history

    window = await ctx.get_prompt_window(session_id)
    assert len(window) == 1
    assert window[0].content[0].text == "hi"

    window_other = await ctx.get_prompt_window("other-session")
    assert len(window_other) == 0


# ---------------------------------------------------------------------------
# SummarizationCompaction — token-based
# ---------------------------------------------------------------------------


def _make_msg(role: str, text: str) -> ChatMessage:
    return ChatMessage(role=role, content=[TextBlock(text=text)])


class _FakeModel:
    """Minimal LLMClient stub that returns a fixed response."""

    def __init__(self, response: str = "summary text") -> None:
        self._response = response
        self.call_count = 0
        self.last_system: str = ""
        self.last_user_text: str = ""

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        self.call_count += 1
        self.last_system = options.system_instructions
        self.last_user_text = " ".join(
            b.text for m in messages for b in m.content if isinstance(b, TextBlock)
        )
        return LLMResponse(content=[TextBlock(text=self._response)], usage=Usage())


@pytest.mark.asyncio
async def test_summarization_no_trigger_when_under_budget():
    """History fits in recent_token_budget → returned unchanged, no LLM call."""
    model = _FakeModel()
    strategy = SummarizationCompaction(
        model, recent_token_budget=10_000, min_old_tokens=100
    )
    msgs = [_make_msg("user", "hello")]
    result = await strategy.compact(msgs)
    assert result is msgs
    assert model.call_count == 0


@pytest.mark.asyncio
async def test_summarization_triggers_on_token_overflow():
    """Old slice exceeds min_old_tokens → LLM called, summary injected at front."""
    model = _FakeModel("User asked about weather.")
    strategy = SummarizationCompaction(
        model,
        recent_token_budget=50,
        min_old_tokens=10,
        chars_per_token=4.0,
    )
    msgs = [_make_msg("user", "a" * 100) for _ in range(6)]
    result = await strategy.compact(msgs)

    assert model.call_count == 1
    assert result[0].role == "system"
    assert "[Earlier conversation summary]" in result[0].content[0].text
    assert "User asked about weather." in result[0].content[0].text
    assert result[-1] in msgs


@pytest.mark.asyncio
async def test_summarization_incremental_update():
    """If the first message in old is already a summary, strategy does an incremental
    update (uses _UPDATE_SYSTEM_PROMPT) rather than re-summarizing from scratch."""
    model = _FakeModel("Updated summary.")
    strategy = SummarizationCompaction(
        model,
        recent_token_budget=50,
        min_old_tokens=10,
        chars_per_token=4.0,
    )

    from agent_substrate.agents.context.compaction.summarization import _make_summary_message

    existing_summary_msg = _make_summary_message("Previous summary of early turns.")
    new_msgs = [_make_msg("user", "b" * 100) for _ in range(5)]
    history = [existing_summary_msg] + new_msgs

    result = await strategy.compact(history)

    assert model.call_count == 1
    assert "Previous summary of early turns." in model.last_user_text
    assert "Updated summary." in result[0].content[0].text


@pytest.mark.asyncio
async def test_summarization_graceful_on_llm_failure():
    """LLM failure → placeholder summary injected, no exception raised."""

    class _FailingModel:
        async def generate(
            self,
            messages: list[ChatMessage],
            *,
            options: GenerationOptions = GenerationOptions(),
        ) -> LLMResponse:
            raise RuntimeError("network error")

    strategy = SummarizationCompaction(
        _FailingModel(),
        recent_token_budget=50,
        min_old_tokens=10,
        chars_per_token=4.0,
    )
    msgs = [_make_msg("user", "c" * 100) for _ in range(6)]
    result = await strategy.compact(msgs)

    assert result[0].role == "system"
    assert "Summary unavailable" in result[0].content[0].text


# ---------------------------------------------------------------------------
# TokenBudgetComposedStrategy.from_model
# ---------------------------------------------------------------------------


def test_from_model_derives_budget_from_registry():
    """from_model reads gpt-4o context_length=128_000 and applies trigger_ratio."""
    strategy = TokenBudgetComposedStrategy.from_model(
        "gpt-4o",
        strategies=[SlidingWindowCompaction(max_messages=100)],
        trigger_ratio=0.80,
    )
    assert strategy._budget == int(128_000 * 0.80)


def test_from_model_uses_default_for_unknown_model():
    """Unknown model name falls back to default_context_length."""
    strategy = TokenBudgetComposedStrategy.from_model(
        "unknown-model-xyz",
        strategies=[SlidingWindowCompaction(max_messages=100)],
        trigger_ratio=0.80,
        default_context_length=32_000,
    )
    assert strategy._budget == int(32_000 * 0.80)


@pytest.mark.asyncio
async def test_from_model_skips_compaction_when_under_budget():
    """from_model strategy returns history unchanged when under token budget."""
    strategy = TokenBudgetComposedStrategy.from_model(
        "gpt-4o",
        strategies=[SlidingWindowCompaction(max_messages=1)],
        trigger_ratio=0.80,
    )
    msgs = [_make_msg("user", "hi")]
    result = await strategy.compact(msgs)
    assert result is msgs
