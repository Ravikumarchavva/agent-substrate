"""Capabilities, modality fitting, tool-arg parsing, and the L1 Chat
Completions encoding of multimodal tool results."""

from __future__ import annotations

import pytest

from substrate.agents.llm import OpenAIChatCompletionClient
from substrate.agents.llm.chat_client import parse_tool_arguments
from substrate.agents.llm.modalities import fit_to_capabilities
from substrate.agents.llm.models import estimate_cost, resolve_capabilities
from substrate.kernel.core.content import (
    ChatMessage,
    MediaBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm import Modality, ModelCapabilities

PNG = b"\x89PNG" + b"\x00" * 16


def _tool_turn(*results: ToolResultBlock) -> list[ChatMessage]:
    return [
        ChatMessage(
            role=Role.ASSISTANT,
            content=[ToolUseBlock(call_id=r.call_id, tool_name=r.name, arguments={}) for r in results],
        ),
        ChatMessage(role=Role.TOOL, content=list(results)),
    ]


def _plot(call_id: str, name: str = "plot") -> ToolResultBlock:
    return ToolResultBlock(
        call_id=call_id,
        name=name,
        content=[TextBlock(text="ok"), MediaBlock.image(data=PNG, media_type="image/png")],
    )


# ── capabilities & pricing ───────────────────────────────────────────────────


def test_unknown_model_defaults_to_text_only_and_free():
    caps = resolve_capabilities("my-local-llama")
    assert caps.input_modalities == frozenset({Modality.TEXT})
    assert caps.cost_usd(Usage(input_tokens=10**6, output_tokens=10**6)) == 0.0


def test_vision_is_derived_from_modalities():
    assert resolve_capabilities("gpt-4o").accepts(Modality.IMAGE)
    assert not resolve_capabilities("o3-mini").accepts(Modality.IMAGE)


def test_cached_tokens_are_billed_at_the_cached_rate_and_never_negative():
    caps = ModelCapabilities(
        model_id="m",
        input_cost_per_mtok=10.0,
        cached_input_cost_per_mtok=1.0,
        output_cost_per_mtok=20.0,
    )
    usage = Usage(input_tokens=1_000_000, cached_tokens=600_000, output_tokens=100_000)
    # 400k fresh @10 + 600k cached @1 + 100k out @20
    assert caps.cost_usd(usage) == pytest.approx(4.0 + 0.6 + 2.0)


def test_estimate_cost_matches_capabilities():
    assert estimate_cost("gpt-4o", 1000, 500) == pytest.approx(0.0075)


# ── fit_to_capabilities ──────────────────────────────────────────────────────


def test_fit_replaces_unsupported_media_including_inside_tool_results():
    caps = ModelCapabilities(model_id="text-only")
    fitted = fit_to_capabilities(_tool_turn(_plot("c1")), caps)

    (result,) = fitted[1].content
    assert not any(isinstance(b, MediaBlock) for b in result.content)
    assert any("image not shown" in getattr(b, "text", "") for b in result.content)


def test_fit_keeps_supported_media_untouched():
    caps = ModelCapabilities(model_id="v", input_modalities=frozenset({Modality.TEXT, Modality.IMAGE}))
    msgs = _tool_turn(_plot("c1"))
    assert fit_to_capabilities(msgs, caps) is not msgs  # new list...
    assert fit_to_capabilities(msgs, caps)[1] is msgs[1]  # ...same unchanged message


# ── tool-call argument parsing ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_args,has_error",
    [
        ('{"a": 1}', {"a": 1}, False),
        ("", {}, False),
        ({"a": 1}, {"a": 1}, False),
        ('{"a": 1', {}, True),  # truncated stream
        ("[1, 2]", {}, True),  # valid JSON, not an object
    ],
)
def test_parse_tool_arguments(raw, expected_args, has_error):
    args, error = parse_tool_arguments(raw)
    assert args == expected_args
    assert (error is not None) is has_error


# ── L1 Chat Completions encoding ─────────────────────────────────────────────


def test_chat_completions_sends_tool_images_after_the_whole_tool_run():
    """Tool messages are text-only in this API, so a tool's image goes in one
    user message placed after ALL the tool messages — never between them."""
    client = OpenAIChatCompletionClient(model="gpt-4o", api_key="x")
    out = client._serialize_messages(_tool_turn(_plot("c1"), _plot("c2")))

    roles = [m["role"] for m in out]
    assert roles == ["assistant", "tool", "tool", "user"]
    images = [p for p in out[-1]["content"] if p["type"] == "image_url"]
    assert len(images) == 2
    assert "attachment(s) follow" in out[1]["content"]


def test_chat_completions_text_only_model_never_gets_an_image_part():
    client = OpenAIChatCompletionClient(model="llama3.2", api_key="x", base_url="http://x/v1")
    out = client._serialize_messages(_tool_turn(_plot("c1")))

    assert [m["role"] for m in out] == ["assistant", "tool"]
    assert "image not shown" in out[1]["content"]


def test_chat_completions_flags_tool_errors():
    err = ToolResultBlock(call_id="c1", name="t", is_error=True, content=[TextBlock(text="boom")])
    out = OpenAIChatCompletionClient(model="gpt-4o", api_key="x")._serialize_messages(_tool_turn(err))
    assert out[1]["content"] == "Error: boom"


def test_chat_completions_documents_use_file_parts():
    doc = MediaBlock(type="document", data=b"%PDF", media_type="application/pdf", filename="a.pdf")
    msg = ChatMessage(role=Role.USER, content=[TextBlock(text="read"), doc])
    out = OpenAIChatCompletionClient(model="gpt-4o", api_key="x")._serialize_messages([msg])
    (file_part,) = [p for p in out[0]["content"] if p["type"] == "file"]
    assert file_part["file"]["filename"] == "a.pdf"


def test_chat_completions_skips_an_empty_assistant_reply():
    """An empty model reply used to serialize as ``content: null`` with no tool
    calls, which the API rejects — breaking every later turn of the session."""
    msgs = [
        ChatMessage(role=Role.USER, content=[TextBlock(text="hi")]),
        ChatMessage(role=Role.ASSISTANT, content=[]),
        ChatMessage(role=Role.USER, content=[TextBlock(text="hello?")]),
    ]
    out = OpenAIChatCompletionClient(model="gpt-4o", api_key="x")._serialize_messages(msgs)
    assert [m["role"] for m in out] == ["user", "user"]
