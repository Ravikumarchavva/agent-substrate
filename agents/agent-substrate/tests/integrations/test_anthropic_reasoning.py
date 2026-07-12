"""Anthropic reasoning round-trip: a persisted ReasoningBlock must re-encode
into the exact wire shape Anthropic requires on continuation.

Anthropic extended thinking + tool use requires the prior assistant turn's
thinking block (with its opaque signature) be replayed verbatim, or the API
rejects the follow-up request. Before ReasoningBlock existed, reasoning was
streamed live and then discarded — this capability was absent by
construction. These tests lock in that the encoder now round-trips it.
"""

from __future__ import annotations

from substrate.integrations.llm.encoders.anthropic import encode_messages
from substrate.kernel.core.content import (
    ChatMessage,
    ReasoningBlock,
    TextBlock,
    ToolUseBlock,
)


def _assistant_blocks(encoded: list[dict]) -> list[dict]:
    assistant = [m for m in encoded if m["role"] == "assistant"]
    assert len(assistant) == 1
    return assistant[0]["content"]


def test_signed_reasoning_encodes_as_thinking_block_first():
    msg = ChatMessage(
        role="assistant",
        content=[
            ReasoningBlock(text="I should call the tool", signature="sig-123"),
            ToolUseBlock(call_id="c1", tool_name="search", arguments={"q": "x"}),
        ],
    )
    _, encoded = encode_messages([msg])
    blocks = _assistant_blocks(encoded)

    # Thinking must be first, carry the signature, and precede the tool_use.
    assert blocks[0] == {
        "type": "thinking",
        "thinking": "I should call the tool",
        "signature": "sig-123",
    }
    assert blocks[1]["type"] == "tool_use"


def test_redacted_reasoning_encodes_as_redacted_thinking():
    msg = ChatMessage(
        role="assistant",
        content=[ReasoningBlock(text="opaque-blob", redacted=True)],
    )
    _, encoded = encode_messages([msg])
    blocks = _assistant_blocks(encoded)
    assert blocks[0] == {"type": "redacted_thinking", "data": "opaque-blob"}


def test_unsigned_reasoning_is_dropped_not_sent_invalid():
    # An unsigned, non-redacted reasoning block (e.g. one that originated from
    # a different provider) would be rejected by Anthropic — the encoder must
    # drop it rather than send an invalid thinking block.
    msg = ChatMessage(
        role="assistant",
        content=[
            ReasoningBlock(text="unsigned summary"),
            TextBlock(text="here is the answer"),
        ],
    )
    _, encoded = encode_messages([msg])
    blocks = _assistant_blocks(encoded)
    assert all(b["type"] != "thinking" for b in blocks)
    assert blocks == [{"type": "text", "text": "here is the answer"}]
