"""The one token estimator, and compaction that must never delete what a tool
returned beyond its text."""

from __future__ import annotations

from substrate.agents.context.compaction import (
    ToolResultCompactionStrategy,
    TruncationStrategy,
)
from substrate.agents.context.tokens import estimate_message_tokens, estimate_tokens
from substrate.kernel.core.content import (
    ChatMessage,
    DataBlock,
    MediaBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


def _msg(role: Role, *blocks) -> ChatMessage:
    return ChatMessage(role=role, content=list(blocks))


def test_text_is_counted_once():
    text = "x" * 4_000
    assert estimate_message_tokens(_msg(Role.USER, TextBlock(text=text))) in range(1_000, 1_010)


def test_an_image_costs_real_tokens_not_a_placeholder_string():
    image = MediaBlock.image(data=b"\x89PNG", media_type="image/png")
    assert estimate_message_tokens(_msg(Role.USER, image)) >= 1_000
    low = MediaBlock.image(data=b"\x89PNG", media_type="image/png", detail="low")
    assert estimate_message_tokens(_msg(Role.USER, low)) < 200


def test_tool_call_arguments_and_tool_result_contents_are_counted():
    code = "print('x')\n" * 1_000  # a big code payload written by the model
    call = _msg(Role.ASSISTANT, ToolUseBlock(call_id="c", tool_name="run", arguments={"code": code}))
    result = _msg(
        Role.TOOL,
        ToolResultBlock(
            call_id="c",
            content=[
                TextBlock(text="ok"),
                DataBlock(data={"rows": list(range(500))}),
                MediaBlock.image(data=b"x", media_type="image/png"),
            ],
        ),
    )
    assert estimate_message_tokens(call) > 2_500
    assert estimate_message_tokens(result) > 1_000  # the image alone


def test_estimate_tokens_sums_messages():
    a, b = _msg(Role.USER, TextBlock(text="a" * 400)), _msg(Role.USER, TextBlock(text="b" * 800))
    assert estimate_tokens([a, b]) == estimate_message_tokens(a) + estimate_message_tokens(b)


async def test_truncating_a_long_tool_result_keeps_its_images_and_data():
    """Regression: an over-long result was rebuilt from its text alone, so a
    chart returned next to long stdout was silently deleted."""
    image = MediaBlock.image(data=b"\x89PNG", media_type="image/png")
    data = DataBlock(data={"rows": 3})
    history = [
        _msg(
            Role.TOOL,
            ToolResultBlock(
                call_id="c1",
                name="run",
                content=[TextBlock(text="z" * 5_000), image, data],
            ),
        )
    ]

    (compacted,) = await ToolResultCompactionStrategy(max_chars=500).compact(history)

    (result,) = compacted.content
    assert any(isinstance(b, MediaBlock) for b in result.content)
    assert any(isinstance(b, DataBlock) for b in result.content)
    assert "chars truncated" in result.text


async def test_character_truncation_counts_images_and_tool_arguments():
    big_call = _msg(Role.ASSISTANT, ToolUseBlock(call_id="c", tool_name="run", arguments={"code": "y" * 3_000}))
    image_msg = _msg(Role.TOOL, ToolResultBlock(call_id="c", content=[MediaBlock.image(data=b"x", media_type="image/png")]))
    tail = _msg(Role.USER, TextBlock(text="thanks"))

    kept = await TruncationStrategy(max_chars=2_000).compact([big_call, image_msg, tail])

    # The 3k-char call and the ~4k-char-equivalent image don't fit; only the tail does.
    assert kept == [tail]


def _history_with_tool_calls() -> list[ChatMessage]:
    def call(i: str) -> ChatMessage:
        return _msg(Role.ASSISTANT, ToolUseBlock(call_id=i, tool_name="t", arguments={}))

    def result(i: str) -> ChatMessage:
        return _msg(Role.TOOL, ToolResultBlock(call_id=i, content=[TextBlock(text="r")]))

    return [
        _msg(Role.USER, TextBlock(text="go")),
        call("c1"), result("c1"),
        call("c2"), result("c2"),
        _msg(Role.ASSISTANT, TextBlock(text="done")),
    ]


async def test_sliding_window_never_starts_on_a_tool_result_without_its_call():
    """Regression: slicing the last N messages could leave a tool result at the
    head with its call cut off, which every provider rejects with a 400."""
    from substrate.agents.context.compaction import SlidingWindowCompaction

    history = _history_with_tool_calls()
    window = await SlidingWindowCompaction(max_messages=2).compact(history)

    # The last two are [tool c2, assistant "done"]; the orphaned result goes.
    assert [m.role for m in window] == [Role.ASSISTANT]
    # A cut that keeps the call keeps its result too.
    window = await SlidingWindowCompaction(max_messages=3).compact(history)
    assert [m.role for m in window] == [Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]


async def test_truncation_never_starts_on_a_tool_result_without_its_call():
    by_count = await TruncationStrategy(max_messages=2).compact(_history_with_tool_calls())
    assert by_count and by_count[0].role != Role.TOOL
