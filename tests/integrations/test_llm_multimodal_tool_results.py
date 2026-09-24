"""A tool's image/document output must reach the model natively, per vendor,
and never be silently dropped or leaked as a placeholder string."""

from __future__ import annotations

import json

from substrate.integrations.llm.anthropic.anthropic_client import AnthropicClient
from substrate.integrations.llm.encoders import openai as openai_enc
from substrate.integrations.llm.gemini.gemini_client import GeminiClient
from substrate.integrations.llm.openai.openai_client import OpenAIClient
from substrate.kernel.core.content import (
    ChatMessage,
    MediaBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _conversation(*results: ToolResultBlock) -> list[ChatMessage]:
    return [
        ChatMessage(
            role=Role.ASSISTANT,
            content=[
                ToolUseBlock(call_id=r.call_id, tool_name=r.name, arguments={})
                for r in results
            ],
        ),
        ChatMessage(role=Role.TOOL, content=list(results)),
    ]


def _plot(call_id: str = "c1") -> ToolResultBlock:
    return ToolResultBlock(
        call_id=call_id,
        name="plot",
        content=[TextBlock(text="chart saved"), MediaBlock.image(data=PNG, media_type="image/png")],
    )


# ── OpenAI Responses API ─────────────────────────────────────────────────────


def test_openai_puts_tool_images_inside_function_call_output():
    _, items = OpenAIClient(model="gpt-4o", api_key="x")._serialize_messages(
        _conversation(_plot())
    )
    (out,) = [i for i in items if i["type"] == "function_call_output"]

    assert [p["type"] for p in out["output"]] == ["input_text", "input_image"]
    assert out["output"][1]["image_url"].startswith("data:image/png;base64,")
    # no separate "attachments" user message, and no placeholder text
    assert not [i for i in items if i["type"] == "message"]
    assert "[Image" not in json.dumps(out)


def test_openai_text_only_tool_result_stays_a_plain_string():
    result = ToolResultBlock(call_id="c1", name="t", content=[TextBlock(text="hi")])
    _, items = OpenAIClient(model="gpt-4o", api_key="x")._serialize_messages(
        _conversation(result)
    )
    (out,) = [i for i in items if i["type"] == "function_call_output"]
    assert out["output"] == "hi"


def test_openai_sends_documents_as_input_file_not_a_stub():
    doc = MediaBlock(type="document", data=b"%PDF-1.4", media_type="application/pdf", filename="r.pdf")
    msg = ChatMessage(role=Role.USER, content=[TextBlock(text="read this"), doc])
    _, items = OpenAIClient(model="gpt-4o", api_key="x")._serialize_messages([msg])

    (part,) = [p for p in items[0]["content"] if p["type"] == "input_file"]
    assert part["filename"] == "r.pdf"
    assert part["file_data"].startswith("data:application/pdf;base64,")


def test_openai_flags_tool_errors():
    err = ToolResultBlock(call_id="c1", name="t", is_error=True, content=[TextBlock(text="boom")])
    _, items = OpenAIClient(model="gpt-4o", api_key="x")._serialize_messages(_conversation(err))
    (out,) = [i for i in items if i["type"] == "function_call_output"]
    assert out["output"] == "Error: boom"


def test_openai_encoding_is_pure_no_network(monkeypatch):
    client = OpenAIClient(model="gpt-4o", api_key="x")

    def _no_network(*a, **k):
        raise AssertionError("serializing a message must not touch the network")

    monkeypatch.setattr(client.client.files, "create", _no_network, raising=False)
    client._serialize_messages(_conversation(_plot()))  # would have uploaded before


def test_openai_encoder_helpers_use_real_api_shapes():
    audio = MediaBlock.audio(data=b"RIFF", media_type="audio/wav")
    assert openai_enc._encode_media_item(audio) == {
        "type": "input_audio",
        "input_audio": {"data": "UklGRg==", "format": "wav"},
    }
    video = MediaBlock.video(data=b"x", media_type="video/mp4")
    assert openai_enc._encode_media_item(video)["type"] == "input_text"  # no video input exists


# ── Anthropic ────────────────────────────────────────────────────────────────


def test_anthropic_tool_result_carries_image_and_error_flag():
    err = ToolResultBlock(call_id="c2", name="plot", is_error=True, content=[TextBlock(text="boom")])
    _, msgs = AnthropicClient(model="claude-sonnet-4", api_key="x")._serialize_messages(
        _conversation(_plot("c1"), err)
    )
    ok, bad = [m["content"][0] for m in msgs if m["role"] == "user"]

    assert [b["type"] for b in ok["content"]] == ["text", "image"]
    assert "is_error" not in ok
    assert bad["is_error"] is True


# ── Gemini ───────────────────────────────────────────────────────────────────


def test_gemini_answers_parallel_calls_in_one_turn():
    err = ToolResultBlock(call_id="c2", name="plot", is_error=True, content=[TextBlock(text="boom")])
    _, contents = GeminiClient(model="gemini-2.5-flash", api_key="x")._serialize_messages(
        _conversation(_plot("c1"), err)
    )
    tool_turn = contents[-1]

    assert tool_turn.role == "user"
    responses = [p.function_response for p in tool_turn.parts if p.function_response]
    assert [r.response for r in responses] == [{"output": "chart saved"}, {"error": "boom"}]


def test_gemini_3_embeds_media_in_the_function_response():
    _, contents = GeminiClient(model="gemini-3.1-flash-lite", api_key="x")._serialize_messages(
        _conversation(_plot())
    )
    (part,) = contents[-1].parts
    assert part.function_response.parts[0].inline_data.mime_type == "image/png"


def test_gemini_2_5_appends_media_to_the_same_turn():
    _, contents = GeminiClient(model="gemini-2.5-flash", api_key="x")._serialize_messages(
        _conversation(_plot())
    )
    fr, media = contents[-1].parts
    assert fr.function_response.parts is None
    assert media.inline_data.mime_type == "image/png"


# ── Text-only model: told, not silently blinded, never sent an image ─────────


def test_text_only_model_gets_a_note_instead_of_the_image():
    _, items = OpenAIClient(model="o3-mini", api_key="x")._serialize_messages(
        _conversation(_plot())
    )
    (out,) = [i for i in items if i["type"] == "function_call_output"]
    assert isinstance(out["output"], str)
    assert "image not shown" in out["output"] and "o3-mini" in out["output"]
