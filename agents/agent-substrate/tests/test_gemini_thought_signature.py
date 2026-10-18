"""Gemini 3 issues a thought signature with each function call and answers 400 ("Function call is missing a thought_signature") if the call is replayed
without it. The signature is kept on the tool call (``extra["gemini"]``), survives being saved, and is sent back with the call."""

from __future__ import annotations

import base64
from types import SimpleNamespace

from substrate.integrations.llm.encoders.gemini import _encode_assistant
from substrate.integrations.llm.gemini.gemini_client import _extra_of
from substrate.types import ChatMessage, Role, TextBlock, ToolUseBlock

SIGNATURE = b"\x00opaque-signature\xff"


def call(**kw) -> ToolUseBlock:
    return ToolUseBlock(
        call_id="c1", tool_name="calculator", arguments={"expression": "6*7"}, **kw
    )


def test_the_signature_on_a_response_part_is_kept_under_geminis_own_key_as_text():
    assert _extra_of(SimpleNamespace(thought_signature=SIGNATURE)) == {
        "gemini": {"thought_signature": base64.b64encode(SIGNATURE).decode()}
    }
    assert _extra_of(SimpleNamespace(thought_signature=None)) == {}
    assert (
        _extra_of(SimpleNamespace()) == {}
    )  # a part with no such field (older models)


def test_a_replayed_call_carries_its_signature_back():
    kept = _extra_of(SimpleNamespace(thought_signature=SIGNATURE))
    msg = ChatMessage(
        role=Role.ASSISTANT,
        content=[TextBlock(text="Let me work that out."), call(extra=kept)],
    )
    content = _encode_assistant(msg)
    function_part = next(p for p in content.parts if p.function_call)
    assert function_part.function_call.name == "calculator"
    assert function_part.thought_signature == SIGNATURE


def test_a_call_with_no_signature_is_replayed_as_before():
    content = _encode_assistant(ChatMessage(role=Role.ASSISTANT, content=[call()]))
    assert content.parts[0].thought_signature is None


def test_the_signature_survives_being_saved_and_loaded():
    block = call(extra={"gemini": {"thought_signature": "c2lnbmF0dXJl"}})
    back = ToolUseBlock.model_validate(block.model_dump(mode="json"))
    assert back.extra["gemini"]["thought_signature"] == "c2lnbmF0dXJl"


def test_another_providers_extras_are_left_alone():
    # A call that came from (or was also marked by) another provider must not confuse Gemini's encoder.
    content = _encode_assistant(
        ChatMessage(
            role=Role.ASSISTANT,
            content=[call(extra={"anthropic": {"signature": "abc"}})],
        )
    )
    assert content.parts[0].thought_signature is None
