from __future__ import annotations

import pytest
from ravi.agents.context import (
    AgentContext,
    DefaultAgentContext,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    SummarizationStrategy,
    TokenBudgetComposedStrategy,
)
from ravi.kernel import AgentId
from ravi.kernel.content import ChatMessage, TextBlock
from ravi.kernel.llm import LLMResponse, Usage
from ravi.kernel.message import Message


@pytest.mark.asyncio
async def test_sliding_window_compaction():
    strategy = SlidingWindowCompaction(max_messages=2)
    agent_id = AgentId(type="test", key="a1")
    messages = [
        Message(target=agent_id, payload=ChatMessage(role="user", content=[TextBlock(text="1")]), sender=agent_id),
        Message(target=agent_id, payload=ChatMessage(role="user", content=[TextBlock(text="2")]), sender=agent_id),
        Message(target=agent_id, payload=ChatMessage(role="user", content=[TextBlock(text="3")]), sender=agent_id),
    ]
    compacted = await strategy.compact(messages)
    assert len(compacted) == 2
    assert compacted[0].payload.content[0].text == "2"
    assert compacted[1].payload.content[0].text == "3"


@pytest.mark.asyncio
async def test_agent_context():
    # Constructor
    history = InMemoryHistoryProvider()
    compaction = SlidingWindowCompaction(max_messages=10)
    ctx = AgentContext(history, compaction)

    assert ctx.history is history
    assert ctx.compaction is compaction

    # Default constructor
    default_ctx = AgentContext.default()
    assert isinstance(default_ctx.history, InMemoryHistoryProvider)
    assert isinstance(default_ctx.compaction, SlidingWindowCompaction)


@pytest.mark.asyncio
async def test_default_agent_context():
    history = InMemoryHistoryProvider()
    compaction = SlidingWindowCompaction(max_messages=10)
    agent_id = AgentId(type="assistant", key="agent_1")
    session_id = "test-session"

    # Append message
    chat_msg = ChatMessage(role="user", content=[TextBlock(text="hi")])
    envelope = Message(target=agent_id, payload=chat_msg, sender=agent_id)
    await history.append(agent_id, envelope, session_id=session_id)

    default_ctx = DefaultAgentContext(agent_id, history, compaction)
    assert default_ctx.agent_id == agent_id
    assert default_ctx.history is history
    assert default_ctx.compaction is compaction

    # Test prompt window retrieval uses session_id
    window = await default_ctx.get_prompt_window(session_id)
    assert len(window) == 1
    assert window[0].payload.content[0].text == "hi"

    # Different session_id yields empty window
    window_other = await default_ctx.get_prompt_window("other-session")
    assert len(window_other) == 0


# ---------------------------------------------------------------------------
# SummarizationStrategy — token-based
# ---------------------------------------------------------------------------


def _make_msg(role: str, text: str, agent_id: AgentId | None = None) -> Message:
    aid = agent_id or AgentId(type="test", key="a")
    return Message(
        target=aid,
        payload=ChatMessage(role=role, content=[TextBlock(text=text)]),
        sender=aid,
    )


class _FakeModel:
    """Minimal LLMClient stub that returns a fixed response."""

    def __init__(self, response: str = "summary text") -> None:
        self._response = response
        self.call_count = 0
        self.last_system: str = ""
        self.last_user_text: str = ""

    async def generate(self, messages, *, tools=None, system="", **kwargs) -> LLMResponse:
        self.call_count += 1
        self.last_system = system
        self.last_user_text = " ".join(
            b.text for m in messages for b in m.content if isinstance(b, TextBlock)
        )
        return LLMResponse(content=[TextBlock(text=self._response)], usage=Usage())


@pytest.mark.asyncio
async def test_summarization_no_trigger_when_under_budget():
    """History fits in recent_token_budget → returned unchanged, no LLM call."""
    model = _FakeModel()
    strategy = SummarizationStrategy(model, recent_token_budget=10_000, min_old_tokens=100)
    msgs = [_make_msg("user", "hello")]
    result = await strategy.compact(msgs)
    assert result is msgs
    assert model.call_count == 0


@pytest.mark.asyncio
async def test_summarization_triggers_on_token_overflow():
    """Old slice exceeds min_old_tokens → LLM called, summary injected at front."""
    model = _FakeModel("User asked about weather.")
    # 4 chars per token; each message ~100 chars → ~25 tokens each
    # recent_token_budget = 50 tokens → keeps ~2 messages verbatim
    # min_old_tokens = 10 → triggers once we have > 10 tokens of old content
    strategy = SummarizationStrategy(
        model,
        recent_token_budget=50,   # ~200 chars
        min_old_tokens=10,
        chars_per_token=4.0,
    )
    msgs = [_make_msg("user", "a" * 100) for _ in range(6)]  # 6 × 100 chars = ~150 tokens total
    result = await strategy.compact(msgs)

    assert model.call_count == 1
    # First message must be the summary envelope
    assert result[0].payload.role == "system"
    assert "[Earlier conversation summary]" in result[0].payload.content[0].text
    assert "User asked about weather." in result[0].payload.content[0].text
    # Recent messages preserved verbatim at the end
    assert result[-1] in msgs


@pytest.mark.asyncio
async def test_summarization_incremental_update():
    """If the first message in old is already a summary, strategy does an incremental
    update (uses _UPDATE_SYSTEM_PROMPT) rather than re-summarizing from scratch."""
    model = _FakeModel("Updated summary.")
    strategy = SummarizationStrategy(
        model,
        recent_token_budget=50,
        min_old_tokens=10,
        chars_per_token=4.0,
    )

    # Build a history that already has a summary at the front, followed by new msgs
    from ravi.agents.context.compaction.summarization import _make_summary_envelope

    existing_summary_msg = _make_summary_envelope("Previous summary of early turns.")
    new_msgs = [_make_msg("user", "b" * 100) for _ in range(5)]
    history = [existing_summary_msg] + new_msgs

    result = await strategy.compact(history)

    assert model.call_count == 1
    # Incremental prompt must reference the existing summary text
    assert "Previous summary of early turns." in model.last_user_text
    # Output is the new summary
    assert "Updated summary." in result[0].payload.content[0].text


@pytest.mark.asyncio
async def test_summarization_graceful_on_llm_failure():
    """LLM failure → placeholder summary injected, no exception raised."""

    class _FailingModel:
        async def generate(self, messages, *, tools=None, system="", **kwargs):
            raise RuntimeError("network error")

    strategy = SummarizationStrategy(
        _FailingModel(),
        recent_token_budget=50,
        min_old_tokens=10,
        chars_per_token=4.0,
    )
    msgs = [_make_msg("user", "c" * 100) for _ in range(6)]
    result = await strategy.compact(msgs)

    assert result[0].payload.role == "system"
    assert "Summary unavailable" in result[0].payload.content[0].text


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
    msgs = [_make_msg("user", "hi")]  # trivially under 102k token budget
    result = await strategy.compact(msgs)
    assert result is msgs
