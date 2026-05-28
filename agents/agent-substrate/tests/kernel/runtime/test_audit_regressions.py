"""Regression tests for the 5 kernel audit findings.

Each test pins a specific behaviour so it can't silently regress:

1. ``BaseAgent.handle_message`` extracts text from ``list[ContentBlock]`` —
   never a Python ``repr`` like ``"[TextBlock(text='hi')]"``.
2. Repeated ``subscribe`` for the same ``(agent_type, topic)`` does NOT
   create duplicate deliveries.
3. ``LocalRuntime`` auto-starts on first send/publish (lifecycle parity
   with remote runtimes).
4. ``Dispatcher.unsubscribe`` removes the topic key once its last
   subscriber is gone — ``topics`` does not leak empty entries.
5. ``LocalRuntime._normalize_content`` coerces malformed list payloads
   into a valid ``list[ContentBlock]`` instead of passing them through.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock


from ravi.kernel.agent_catalog import AgentCatalog
from ravi.kernel.agents.base_agent import BaseAgent
from ravi.kernel.agents.agent_result import AgentRunResult
from ravi.kernel.messages.content import ImageBlock, TextBlock, blocks_to_text
from ravi.kernel.runtime import AgentId, LocalRuntime, MessageContext, TopicId


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CaptureAgent(BaseAgent):
    """BaseAgent whose ``run`` records the input it received."""

    def __init__(self) -> None:
        catalog = AgentCatalog()
        catalog.register_model("primary", MagicMock())
        super().__init__(name="capture", description="captures input", catalog=catalog)
        self.received_inputs: list[str] = []

    async def run(self, input_text: str, **kwargs: Any) -> AgentRunResult:  # type: ignore[override]
        self.received_inputs.append(input_text)
        return AgentRunResult(agent_name=self.name, output=[input_text])

    def run_stream(self, input_text: str, **kwargs: Any):  # type: ignore[override]
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Finding 1 — BaseAgent.handle_message must not stringify ContentBlock list
# ---------------------------------------------------------------------------


class TestHandleMessageContentExtraction:
    """``handle_message`` extracts text from blocks; never a Python repr."""

    async def test_list_of_text_blocks_is_extracted(self) -> None:
        agent = _CaptureAgent()
        ctx = MessageContext(
            runtime=MagicMock(),
            sender=AgentId("x", "1"),
            correlation_id="c",
            agent_id=AgentId("capture", "1"),
        )
        await agent.handle_message(ctx, [TextBlock(text="hello world")])
        assert agent.received_inputs == ["hello world"]
        # Crucially: no Python repr leaks through.
        assert "TextBlock" not in agent.received_inputs[0]

    async def test_multiple_text_blocks_are_joined(self) -> None:
        agent = _CaptureAgent()
        ctx = MessageContext(
            runtime=MagicMock(),
            sender=None,
            correlation_id="c",
            agent_id=AgentId("capture", "1"),
        )
        await agent.handle_message(
            ctx, [TextBlock(text="hello"), TextBlock(text="world")]
        )
        assert agent.received_inputs == ["hello world"]

    async def test_mixed_blocks_keep_placeholders(self) -> None:
        agent = _CaptureAgent()
        ctx = MessageContext(
            runtime=MagicMock(),
            sender=None,
            correlation_id="c",
            agent_id=AgentId("capture", "1"),
        )
        await agent.handle_message(
            ctx,
            [
                TextBlock(text="hi"),
                ImageBlock(data="iVBORw0KGgo=", media_type="image/png"),
            ],
        )
        # Image is summarised as "[image]" — not its full repr.
        assert agent.received_inputs == ["hi [image]"]
        assert "data=" not in agent.received_inputs[0]

    async def test_bare_string_payload_works(self) -> None:
        agent = _CaptureAgent()
        ctx = MessageContext(
            runtime=MagicMock(),
            sender=None,
            correlation_id="c",
            agent_id=AgentId("capture", "1"),
        )
        await agent.handle_message(ctx, "plain")
        assert agent.received_inputs == ["plain"]


class TestBlocksToTextHelper:
    """``blocks_to_text`` is the canonical multimodal → text reducer."""

    def test_empty_list_returns_empty_string(self) -> None:
        assert blocks_to_text([]) == ""

    def test_only_text_blocks(self) -> None:
        assert blocks_to_text([TextBlock(text="a"), TextBlock(text="b")]) == "a b"

    def test_custom_separator(self) -> None:
        assert (
            blocks_to_text(
                [TextBlock(text="a"), TextBlock(text="b")],
                separator="\n",
            )
            == "a\nb"
        )

    def test_image_placeholder_short(self) -> None:
        text = blocks_to_text(
            [TextBlock(text="hi"), ImageBlock(data="abc", media_type="image/png")]
        )
        assert text == "hi [image]"


# ---------------------------------------------------------------------------
# Finding 2 — Duplicate subscribe creates duplicate deliveries
# ---------------------------------------------------------------------------


class TestSubscribeIsIdempotent:
    async def test_double_subscribe_delivers_once(self) -> None:
        rt = LocalRuntime()
        delivered: list[str] = []

        async def handler(ctx: MessageContext, payload: list) -> None:
            delivered.append(blocks_to_text(payload))

        topic = TopicId("events", "session-1")
        await rt.register("listener", handler)
        await rt.subscribe("listener", topic)
        await rt.subscribe("listener", topic)  # duplicate — must NOT double-deliver
        await rt.subscribe("listener", topic)  # triple — same

        await rt.publish_message("hello", sender=AgentId("pub", "1"), topic=topic)

        # Drain
        await rt.stop_when_idle(poll_interval=0.01)

        assert delivered == ["hello"], (
            f"Duplicate subscribe must not duplicate delivery, got: {delivered}"
        )

    async def test_double_subscribe_then_unsubscribe_idempotent(self) -> None:
        """Idempotent subscribe leaves exactly one Subscription record."""
        rt = LocalRuntime()

        async def handler(ctx: MessageContext, payload: list) -> None:
            pass

        topic = TopicId("events", "s1")
        await rt.register("listener", handler)
        await rt.subscribe("listener", topic)
        await rt.subscribe("listener", topic)
        await rt.subscribe("listener", topic)

        subs = rt._dispatcher._topic_subscribers.get(topic, [])
        assert len(subs) == 1, (
            f"Expected exactly 1 subscription after triple subscribe; got {len(subs)}"
        )


# ---------------------------------------------------------------------------
# Finding 3 — LocalRuntime must behave consistently around lifecycle
# ---------------------------------------------------------------------------


class TestLifecycleAutoStart:
    async def test_send_without_explicit_start_works_and_marks_started(self) -> None:
        rt = LocalRuntime()
        assert rt._started is False

        async def handler(ctx: MessageContext, payload: list) -> str:
            return "ok"

        await rt.register("worker", handler)
        result = await rt.send_message(
            "task",
            sender=AgentId("x", "1"),
            recipient=AgentId("worker", "1"),
        )
        assert result == "ok"
        assert rt._started is True, "send_message must auto-start the runtime"
        await rt.stop()

    async def test_publish_without_explicit_start_works(self) -> None:
        rt = LocalRuntime()
        delivered: list[str] = []

        async def handler(ctx: MessageContext, payload: list) -> None:
            delivered.append(blocks_to_text(payload))

        topic = TopicId("events", "s1")
        await rt.register("listener", handler)
        await rt.subscribe("listener", topic)

        await rt.publish_message("hi", sender=AgentId("pub", "1"), topic=topic)
        assert rt._started is True
        await rt.stop_when_idle(poll_interval=0.01)
        assert delivered == ["hi"]

    async def test_repeated_start_is_idempotent(self) -> None:
        rt = LocalRuntime()
        await rt.start()
        await rt.start()  # must not raise
        await rt.start()
        assert rt._started is True
        await rt.stop()


# ---------------------------------------------------------------------------
# Finding 4 — Dispatcher.unsubscribe must clean up empty topic keys
# ---------------------------------------------------------------------------


class TestDispatcherUnsubscribeCleanup:
    async def test_unsubscribe_last_subscriber_removes_topic(self) -> None:
        rt = LocalRuntime()
        topic = TopicId("events", "session-1")

        async def handler(ctx: MessageContext, payload: list) -> None:
            pass

        await rt.register("listener", handler)
        await rt.subscribe("listener", topic)
        assert topic in rt._dispatcher.topics

        # Find the sub id and remove
        sub_id = rt._dispatcher._topic_subscribers[topic][0].id
        rt._dispatcher.unsubscribe(sub_id)

        assert topic not in rt._dispatcher.topics, (
            "Topic key must be removed when last subscriber unsubscribes"
        )

    async def test_unsubscribe_keeps_other_subscribers(self) -> None:
        rt = LocalRuntime()
        topic = TopicId("events", "session-1")

        async def h1(ctx: MessageContext, payload: list) -> None:
            pass

        async def h2(ctx: MessageContext, payload: list) -> None:
            pass

        await rt.register("a", h1)
        await rt.register("b", h2)
        await rt.subscribe("a", topic)
        await rt.subscribe("b", topic)

        sub_a_id = next(
            s.id
            for s in rt._dispatcher._topic_subscribers[topic]
            if s.agent_type == "a"
        )
        rt._dispatcher.unsubscribe(sub_a_id)

        remaining_types = [
            s.agent_type for s in rt._dispatcher._topic_subscribers[topic]
        ]
        assert remaining_types == ["b"]
        assert topic in rt._dispatcher.topics


# ---------------------------------------------------------------------------
# Finding 5 — _normalize_content must validate / coerce list elements
# ---------------------------------------------------------------------------


class TestNormalizeContentValidation:
    def test_list_of_strings_is_coerced(self) -> None:
        out = LocalRuntime._normalize_content(["foo", "bar"])
        assert all(isinstance(b, TextBlock) for b in out)
        assert [b.text for b in out] == ["foo", "bar"]

    def test_list_of_random_objects_is_coerced(self) -> None:
        out = LocalRuntime._normalize_content([1, {"k": "v"}, 3.14])
        assert all(isinstance(b, TextBlock) for b in out)
        # Each non-block element became a TextBlock with str(...) of it.
        assert out[0].text == "1"
        assert out[2].text == "3.14"

    def test_proper_blocks_pass_through(self) -> None:
        blocks = [TextBlock(text="hi"), ImageBlock(data="x", media_type="image/png")]
        out = LocalRuntime._normalize_content(blocks)
        assert out == blocks

    def test_mixed_list_blocks_and_strings(self) -> None:
        out = LocalRuntime._normalize_content([TextBlock(text="hi"), "bye"])
        assert isinstance(out[0], TextBlock) and out[0].text == "hi"
        assert isinstance(out[1], TextBlock) and out[1].text == "bye"


if __name__ == "__main__":
    asyncio.run(asyncio.sleep(0))  # silence "unused asyncio" lint
