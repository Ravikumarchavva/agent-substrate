"""project_thread() — conversation history projected from the EventLog,
the single source of truth (replaces the old steps-table write path).

Covers the crash-mid-run motivation directly: a thread's SECOND run (e.g.
after a crash-and-resume) must appear in the projection too — nothing here
depends on any run being the "currently active" one.
"""

from __future__ import annotations

from substrate.agents.core.react import ReActAgent
from substrate.agents.runtime import Runtime
from substrate.kernel.core.content import TextBlock
from substrate.kernel.core.usage import Usage
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta
from substrate.serving.protocol.events import (
    RunCompletedEvent,
    TextDeltaEvent,
    UserMessageEvent,
)
from substrate.serving.stream.history import project_thread


class _StubLLM:
    def __init__(self, answer: str) -> None:
        self.model = "stub"
        self._answer = answer

    async def generate_stream(self, messages, *, options, ctx=None):
        yield TextDelta(text=self._answer)
        yield CompletionEvent(content=[TextBlock(text=self._answer)], usage=Usage())


async def test_project_thread_returns_one_runs_full_conversation() -> None:
    agent = ReActAgent("assistant", model=_StubLLM("hi there"))
    thread_id = "thread-1"
    async with Runtime() as rt:
        await rt.register(agent)
        from substrate.kernel.core.content import ChatMessage, Role
        from substrate.kernel.messaging.message import ChatPayload, Message

        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hello")])
            ),
            correlation_id=thread_id,
        )
        run_id = await rt.submit(agent.id, msg, thread_id=thread_id, max_retries=0)
        async for entry in rt.event_log.tail(run_id):
            if entry.kind == "run.completed":
                break

        events = await project_thread(rt.event_log, rt.scheduler, thread_id)

    assert isinstance(events[0], UserMessageEvent)
    assert events[0].text == "hello"
    assert any(isinstance(e, TextDeltaEvent) and e.text == "hi there" for e in events)


async def test_project_thread_spans_multiple_runs_in_order() -> None:
    """Two separate runs on the same thread (e.g. two chat turns, or a crash
    between them) both appear, oldest first."""
    agent = ReActAgent("assistant", model=_StubLLM("second answer"))
    thread_id = "thread-multi"
    from substrate.kernel.core.content import ChatMessage, Role
    from substrate.kernel.messaging.message import ChatPayload, Message

    async with Runtime() as rt:
        await rt.register(agent)

        msg1 = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="first")])
            ),
            correlation_id=thread_id,
        )
        run1 = await rt.submit(agent.id, msg1, thread_id=thread_id, max_retries=0)
        async for entry in rt.event_log.tail(run1):
            if entry.kind == "run.completed":
                break

        msg2 = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="second")])
            ),
            correlation_id=thread_id,
        )
        run2 = await rt.submit(agent.id, msg2, thread_id=thread_id, max_retries=0)
        async for entry in rt.event_log.tail(run2):
            if entry.kind == "run.completed":
                break

        events = await project_thread(rt.event_log, rt.scheduler, thread_id)

    user_texts = [e.text for e in events if isinstance(e, UserMessageEvent)]
    assert user_texts == ["first", "second"], "both runs' turns, in order"


async def test_project_thread_skips_non_streaming_log_kinds() -> None:
    """run.started/effect.result/llm.call never leak into the projection."""
    agent = ReActAgent("assistant", model=_StubLLM("ok"))
    thread_id = "thread-filter"
    from substrate.kernel.core.content import ChatMessage, Role
    from substrate.kernel.messaging.message import ChatPayload, Message

    async with Runtime() as rt:
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
            ),
            correlation_id=thread_id,
        )
        run_id = await rt.submit(agent.id, msg, thread_id=thread_id, max_retries=0)
        async for entry in rt.event_log.tail(run_id):
            if entry.kind == "run.completed":
                break

        events = await project_thread(rt.event_log, rt.scheduler, thread_id)

    assert not any(isinstance(e, RunCompletedEvent) for e in events), (
        "run lifecycle kinds are not streaming kinds, must not appear"
    )
