"""GenerationOptions.reasoning maps onto each vendor's own control, is ignored
for models that don't reason, and reasoning output never leaks into the answer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import BaseModel

from substrate.agents.llm import OpenAIChatCompletionClient
from substrate.integrations.llm.anthropic.anthropic_client import AnthropicClient
from substrate.integrations.llm.gemini.gemini_client import GeminiClient
from substrate.integrations.llm.openai.openai_client import OpenAIClient
from substrate.kernel.core.content import ChatMessage, ReasoningBlock, Role, TextBlock
from substrate.kernel.llm import GenerationOptions, ReasoningEffort

MSGS = [ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])]
HIGH = GenerationOptions(reasoning=ReasoningEffort.HIGH)


class _Tool:
    name = "t"
    description = "d"
    input_schema: dict[str, object] = {"type": "object", "properties": {}}


class _Schema(BaseModel):
    answer: str


# ── OpenAI Responses API ─────────────────────────────────────────────────────


def test_openai_reasoning_effort_and_summary_for_a_reasoning_model():
    params = OpenAIClient(model="o3", api_key="x")._build_params(MSGS, HIGH, stream=False)
    assert params["reasoning"] == {"effort": "high", "summary": "auto"}
    assert "temperature" not in params  # reasoning models reject it


def test_openai_reasoning_is_opt_in_and_ignored_for_non_reasoning_models():
    o3 = OpenAIClient(model="o3", api_key="x")
    assert "reasoning" not in o3._build_params(MSGS, GenerationOptions(), stream=False)
    assert "reasoning" not in OpenAIClient(model="gpt-4o", api_key="x")._build_params(
        MSGS, HIGH, stream=False
    )


def test_openai_off_maps_to_the_lowest_effort_the_model_accepts():
    off = GenerationOptions(reasoning=ReasoningEffort.OFF)
    gpt5 = OpenAIClient(model="gpt-5", api_key="x")._build_params(MSGS, off, stream=False)
    o3 = OpenAIClient(model="o3", api_key="x")._build_params(MSGS, off, stream=False)
    assert gpt5["reasoning"] == {"effort": "minimal"}
    assert o3["reasoning"] == {"effort": "low"}


def test_openai_streaming_and_non_streaming_build_the_same_request():
    client = OpenAIClient(model="gpt-4o", api_key="x")
    opts = GenerationOptions(response_format=_Schema, max_tokens=50)
    plain = client._build_params(MSGS, opts, stream=False)
    streamed = client._build_params(MSGS, opts, stream=True)

    assert streamed.pop("stream") is True
    assert plain == streamed
    # Regression: the streaming path used to skip the schema unless tools were present.
    assert "text" in streamed and streamed["max_output_tokens"] == 50


def test_openai_parses_reasoning_summary_ahead_of_the_answer():
    response = SimpleNamespace(
        output_text="42",
        output=[
            SimpleNamespace(
                type="reasoning",
                summary=[SimpleNamespace(text="thought A"), SimpleNamespace(text="thought B")],
            )
        ],
    )
    blocks = OpenAIClient(model="o3", api_key="x")._parse_output(response, None)
    assert isinstance(blocks[0], ReasoningBlock) and blocks[0].text == "thought A\n\nthought B"
    assert blocks[1].text == "42"


def test_openai_usage_reads_the_responses_api_field_names():
    usage = OpenAIClient._extract_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=1000,
                input_tokens_details=SimpleNamespace(cached_tokens=600),
                output_tokens=200,
                output_tokens_details=SimpleNamespace(reasoning_tokens=150),
            )
        )
    )
    assert (usage.input_tokens, usage.cached_tokens, usage.output_tokens, usage.reasoning_tokens) == (
        1000, 600, 200, 150,
    )


# ── Chat Completions (L1 default) ────────────────────────────────────────────


def test_chat_completions_sends_reasoning_effort_only_to_openai_itself():
    openai = OpenAIChatCompletionClient(model="o3", api_key="x")
    other = OpenAIChatCompletionClient(model="o3", api_key="x", base_url="http://local/v1")
    assert openai._build_params(MSGS, HIGH, stream=False)["reasoning_effort"] == "high"
    assert "reasoning_effort" not in other._build_params(MSGS, HIGH, stream=False)


# ── Anthropic ────────────────────────────────────────────────────────────────


def test_anthropic_reasoning_becomes_a_thinking_budget_and_drops_temperature():
    params = AnthropicClient(model="claude-sonnet-4", api_key="x")._build_params(MSGS, HIGH)
    assert params["thinking"] == {"type": "enabled", "budget_tokens": 24_000}
    assert params["max_tokens"] > 24_000  # the API rejects max_tokens <= the budget
    assert "temperature" not in params


def test_anthropic_ignores_reasoning_on_a_model_without_thinking():
    params = AnthropicClient(model="claude-3-5-sonnet", api_key="x")._build_params(MSGS, HIGH)
    assert "thinking" not in params and "temperature" in params


def test_anthropic_explicit_extra_wins_over_the_typed_level():
    opts = GenerationOptions(reasoning=ReasoningEffort.LOW, extra={"thinking_budget": 5_000})
    params = AnthropicClient(model="claude-sonnet-4", api_key="x")._build_params(MSGS, opts)
    assert params["thinking"]["budget_tokens"] == 5_000


def test_anthropic_drops_a_forced_tool_choice_while_thinking():
    opts = GenerationOptions(
        reasoning=ReasoningEffort.MEDIUM, tools=[_Tool()], tool_choice="required"
    )
    params = AnthropicClient(model="claude-sonnet-4", api_key="x")._build_params(MSGS, opts)
    assert "tool_choice" not in params  # the API rejects any/tool with thinking on


def test_anthropic_input_tokens_include_cached_ones():
    usage = AnthropicClient._usage(input_tokens=100, cache_read=900, cache_creation=50, output_tokens=7)
    assert (usage.input_tokens, usage.cached_tokens) == (1050, 900)


# ── Gemini ───────────────────────────────────────────────────────────────────


def test_gemini_reasoning_sets_a_thinking_budget_and_asks_for_thoughts():
    _, config = GeminiClient(model="gemini-2.5-flash", api_key="x")._build_request(
        MSGS, GenerationOptions(reasoning=ReasoningEffort.MEDIUM)
    )
    assert config.thinking_config.thinking_budget == 8_192
    assert config.thinking_config.include_thoughts is True


def test_gemini_leaves_thinking_at_the_model_default_when_unset():
    _, config = GeminiClient(model="gemini-2.5-flash", api_key="x")._build_request(
        MSGS, GenerationOptions()
    )
    assert config.thinking_config is None


def test_gemini_keeps_thinking_off_on_tool_turns_whatever_is_asked():
    _, config = GeminiClient(model="gemini-2.5-flash", api_key="x")._build_request(
        MSGS, GenerationOptions(reasoning=ReasoningEffort.HIGH, tools=[_Tool()])
    )
    assert config.thinking_config.thinking_budget == 0


async def test_gemini_thought_parts_become_reasoning_not_answer_text():
    client = GeminiClient(model="gemini-2.5-flash", api_key="x")
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text="let me think", thought=True, function_call=None),
                        SimpleNamespace(text="The answer is 4.", thought=None, function_call=None),
                    ]
                )
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            thoughts_token_count=20,
            cached_content_token_count=0,
        ),
    )
    client.client.aio.models.generate_content = AsyncMock(return_value=response)

    result = await client.generate(MSGS)

    assert [type(b).__name__ for b in result.content] == ["ReasoningBlock", "TextBlock"]
    assert result.text == "The answer is 4."
    assert result.usage.output_tokens == 25 and result.usage.reasoning_tokens == 20
