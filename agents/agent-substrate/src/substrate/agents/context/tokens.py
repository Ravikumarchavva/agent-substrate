"""One token estimator for every context/compaction decision.

An estimate, not a count: text is sized by ``chars_per_token``; everything
else that goes over the wire is counted too — tool-call arguments (often the
largest thing an agent writes, e.g. a code payload), tool-result contents, and
media, which costs far more than any placeholder string suggests.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from substrate.kernel.core.content import (
    ChatMessage,
    ContentBlock,
    DataBlock,
    ErrorBlock,
    MediaBlock,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UnknownBlock,
)

DEFAULT_CHARS_PER_TOKEN = 4.0

# Media has no honest per-byte price; these are middling provider figures
# (OpenAI ~765 for a 1024² high-detail image, Anthropic ~1_400, Gemini 258).
_IMAGE_TOKENS = 1_000
_LOW_DETAIL_IMAGE_TOKENS = 100
_OTHER_MEDIA_TOKENS = 1_500
_MESSAGE_OVERHEAD_TOKENS = 4


def _json_chars(value: object) -> int:
    return len(json.dumps(value, default=str))


def _block_cost(block: ContentBlock) -> tuple[int, int]:
    """(text characters, fixed tokens) for one block."""
    if isinstance(block, (TextBlock, ReasoningBlock)):
        return len(block.text), 0
    if isinstance(block, ToolUseBlock):
        return len(block.tool_name) + _json_chars(block.arguments), 0
    if isinstance(block, DataBlock):
        return _json_chars(block.data), 0
    if isinstance(block, ErrorBlock):
        return len(block.error_type) + len(block.message), 0
    if isinstance(block, MediaBlock):
        if block.type == "image":
            return 0, _LOW_DETAIL_IMAGE_TOKENS if block.detail == "low" else _IMAGE_TOKENS
        return 0, _OTHER_MEDIA_TOKENS
    if isinstance(block, ToolResultBlock):
        chars = fixed = 0
        for inner in block.content:
            c, f = _block_cost(inner)
            chars, fixed = chars + c, fixed + f
        return chars, fixed
    if isinstance(block, UnknownBlock):
        return _json_chars(block.raw), 0
    return len(str(block)), 0


def _message_cost(msg: ChatMessage) -> tuple[int, int]:
    chars = fixed = 0
    for block in msg.content:
        c, f = _block_cost(block)
        chars, fixed = chars + c, fixed + f
    return chars, fixed + _MESSAGE_OVERHEAD_TOKENS


def estimate_message_tokens(
    msg: ChatMessage, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN
) -> int:
    chars, fixed = _message_cost(msg)
    return int(chars / chars_per_token) + fixed


def estimate_tokens(
    messages: Sequence[ChatMessage], chars_per_token: float = DEFAULT_CHARS_PER_TOKEN
) -> int:
    return sum(estimate_message_tokens(m, chars_per_token) for m in messages)


def estimate_message_chars(msg: ChatMessage) -> int:
    """Character-equivalent size, for character budgets: media counts as the
    text it would cost in tokens at the default ratio."""
    chars, fixed = _message_cost(msg)
    return chars + int(fixed * DEFAULT_CHARS_PER_TOKEN)


__all__ = [
    "DEFAULT_CHARS_PER_TOKEN",
    "estimate_message_chars",
    "estimate_message_tokens",
    "estimate_tokens",
]
