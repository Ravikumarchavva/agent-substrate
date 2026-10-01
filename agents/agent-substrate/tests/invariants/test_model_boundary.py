"""Invariant register — the model boundary (rows I16, I18, I19).

These protect work already done: an earlier pass fixed six places where a
tool's images were dropped or degraded on the way to the model, and fixed
compaction deleting them. Those fixes live in code the rewrite replaces, so
without rows here they are exactly the kind of thing a rewrite quietly loses.

Written as properties over generated conversations rather than examples,
because the failure modes were all shape-dependent — a particular window
boundary, a particular block order.
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from substrate.kernel.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.kernel.context.compaction.tool_result import ToolResultCompactionStrategy
from substrate.kernel.llm.modalities import fit_to_capabilities
from substrate.kernel.abstractions.core.content import (
    ChatMessage,
    MediaBlock,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.abstractions.core.usage import Usage
from substrate.kernel.abstractions.llm import Modality, ModelCapabilities

# --------------------------------------------------------------------------
# Generated conversations containing tool calls and tool-result media
# --------------------------------------------------------------------------


def _image(seed: int) -> MediaBlock:
    return MediaBlock.image(data=bytes([seed % 256]) * 8, media_type="image/png")


@st.composite
def _conversation(draw: st.DrawFn) -> list[ChatMessage]:
    """A conversation of user turns and assistant tool-call/tool-result pairs,
    some of whose results carry images."""
    turns = draw(st.integers(min_value=1, max_value=6))
    messages: list[ChatMessage] = []
    for turn in range(turns):
        messages.append(
            ChatMessage(role=Role.USER, content=[TextBlock(text=f"ask {turn}")])
        )
        if draw(st.booleans()):
            call_id = f"c{turn}"
            messages.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=[ToolUseBlock(call_id=call_id, tool_name="chart")],
                )
            )
            content: list[object] = [TextBlock(text="x" * draw(st.integers(0, 2000)))]
            if draw(st.booleans()):
                content.append(_image(turn))
            messages.append(
                ChatMessage(
                    role=Role.TOOL,
                    content=[
                        ToolResultBlock(call_id=call_id, name="chart", content=content)  # type: ignore[arg-type]
                    ],
                )
            )
        else:
            messages.append(
                ChatMessage(role=Role.ASSISTANT, content=[TextBlock(text=f"answer {turn}")])
            )
    return messages


def _media_count(messages: list[ChatMessage]) -> int:
    total = 0
    for message in messages:
        for block in message.content:
            if isinstance(block, MediaBlock):
                total += 1
            elif isinstance(block, ToolResultBlock):
                total += sum(1 for inner in block.content if isinstance(inner, MediaBlock))
    return total


def _orphaned_tool_results(messages: list[ChatMessage]) -> list[str]:
    """Tool results whose originating tool call is not in the window.

    Every provider rejects these outright, which is what made long runs fail.
    """
    offered = {
        block.call_id
        for message in messages
        for block in message.content
        if isinstance(block, ToolUseBlock)
    }
    return [
        block.call_id
        for message in messages
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.call_id not in offered
    ]


# --------------------------------------------------------------------------
# I18 — compaction
# --------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(messages=_conversation(), window=st.integers(min_value=1, max_value=12))
def test_i18_compaction_never_leaves_an_orphaned_tool_result(
    messages: list[ChatMessage], window: int
) -> None:
    """A window that starts mid-turn must not keep a tool result whose call it
    dropped."""
    kept = asyncio.run(SlidingWindowCompaction(max_messages=window).compact(list(messages)))
    orphans = _orphaned_tool_results(kept)
    assert not orphans, (
        f"compaction to a {window}-message window left tool results with no "
        f"matching call: {orphans}"
    )


@settings(max_examples=150, deadline=None)
@given(messages=_conversation(), max_chars=st.integers(min_value=1, max_value=200))
def test_i18_truncating_a_tool_result_keeps_its_media(
    messages: list[ChatMessage], max_chars: int
) -> None:
    """Truncation is about text length. An image is not text and must survive
    it — this is the regression that made the model blind to its own charts."""
    before = _media_count(messages)
    kept = asyncio.run(ToolResultCompactionStrategy(max_chars=max_chars).compact(list(messages)))
    assert _media_count(kept) == before, (
        f"truncating tool-result text to {max_chars} chars dropped media: "
        f"{before} -> {_media_count(kept)}"
    )


# --------------------------------------------------------------------------
# I16 — capability filtering
# --------------------------------------------------------------------------


_MODALITIES = st.sets(
    st.sampled_from([Modality.TEXT, Modality.IMAGE, Modality.DOCUMENT, Modality.AUDIO]),
    min_size=1,
)


@settings(max_examples=150, deadline=None)
@given(messages=_conversation(), modalities=_MODALITIES)
def test_i16_no_content_outside_the_models_modalities_survives(
    messages: list[ChatMessage], modalities: set[Modality]
) -> None:
    """A client must never hand a provider content the model cannot accept —
    the provider either rejects the request or silently ignores the content."""
    caps = ModelCapabilities(model_id="m", input_modalities=frozenset(modalities))
    fitted = fit_to_capabilities(list(messages), caps)

    def _blocks(msgs: list[ChatMessage]) -> list[object]:
        out: list[object] = []
        for message in msgs:
            for block in message.content:
                out.append(block)
                if isinstance(block, ToolResultBlock):
                    out.extend(block.content)
        return out

    for block in _blocks(fitted):
        if isinstance(block, MediaBlock):
            assert Modality(block.type) in modalities, (
                f"{block.type} content survived for a model accepting only "
                f"{sorted(m.value for m in modalities)}"
            )


@settings(max_examples=100, deadline=None)
@given(messages=_conversation())
def test_i16_dropped_content_leaves_a_note(messages: list[ChatMessage]) -> None:
    """Dropping media silently makes the model answer as if it never existed.
    It has to be told something was there."""
    caps = ModelCapabilities(model_id="m", input_modalities=frozenset({Modality.TEXT}))
    had_media = _media_count(messages) > 0
    fitted = fit_to_capabilities(list(messages), caps)
    if had_media:
        text = " ".join(
            block.text
            for message in fitted
            for block in message.content
            if isinstance(block, TextBlock)
        ) + " ".join(
            inner.text
            for message in fitted
            for block in message.content
            if isinstance(block, ToolResultBlock)
            for inner in block.content
            if isinstance(inner, TextBlock)
        )
        assert text.strip(), "all media was removed and nothing was said about it"


# --------------------------------------------------------------------------
# I19 — usage and cost arithmetic
# --------------------------------------------------------------------------


_usages = st.builds(
    Usage,
    input_tokens=st.integers(min_value=0, max_value=10**6),
    cached_tokens=st.integers(min_value=0, max_value=10**6),
    output_tokens=st.integers(min_value=0, max_value=10**6),
    reasoning_tokens=st.integers(min_value=0, max_value=10**6),
)


@settings(max_examples=200, deadline=None)
@given(usages=st.lists(_usages, min_size=1, max_size=20))
def test_i19_accumulated_usage_equals_the_sum_of_its_parts(usages: list[Usage]) -> None:
    """Budgets are enforced against accumulated usage; the accumulation has to
    be exact."""
    total = usages[0]
    for usage in usages[1:]:
        total = total + usage
    assert total.input_tokens == sum(u.input_tokens for u in usages)
    assert total.output_tokens == sum(u.output_tokens for u in usages)
    assert total.cached_tokens == sum(u.cached_tokens for u in usages)
    assert total.total_tokens == total.input_tokens + total.output_tokens


@settings(max_examples=200, deadline=None)
@given(
    usage=_usages,
    input_rate=st.floats(min_value=0, max_value=100, allow_nan=False),
    output_rate=st.floats(min_value=0, max_value=100, allow_nan=False),
    cached_rate=st.floats(min_value=0, max_value=100, allow_nan=False),
)
def test_i19_cost_is_never_negative_and_rises_with_usage(
    usage: Usage, input_rate: float, output_rate: float, cached_rate: float
) -> None:
    """Cost drives the budget that stops a runaway agent. A negative or
    non-monotonic cost disables it."""
    caps = ModelCapabilities(
        model_id="m",
        input_cost_per_mtok=input_rate,
        output_cost_per_mtok=output_rate,
        cached_input_cost_per_mtok=cached_rate,
    )
    cost = caps.cost_usd(usage)
    assert cost >= 0.0
    more = Usage(
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cached_tokens,
        output_tokens=usage.output_tokens + 1000,
        reasoning_tokens=usage.reasoning_tokens,
    )
    assert caps.cost_usd(more) >= cost
