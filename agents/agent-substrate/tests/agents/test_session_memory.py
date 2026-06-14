"""Tests for session-scoped history — cross-turn memory via the Runtime."""

from __future__ import annotations

from typing import AsyncIterator

from ravi.agents.context import (
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.agents.core import ReActAgent
from ravi.agents.runtime import Runtime
from ravi.kernel import ChatMessage, ContentBlock, TextBlock
from ravi.kernel.core.content import Role
from ravi.kernel.core.identity import AgentId
from ravi.kernel.llm import GenerationOptions, LLMResponse, Usage
from ravi.kernel.messaging.message import ChatPayload, Message
from ravi.kernel.messaging.stream import CompletionEvent, TextDelta
from ravi.serving.shared.execution.agent_factory import load_session_memory


# ---------------------------------------------------------------------------
# Mock LLM
# ---------------------------------------------------------------------------


class MockLLMClient:
    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._queue = list(responses)
        self.model = "mock-model"

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        assert self._queue, "MockLLMClient: no more scripted responses"
        return LLMResponse(content=self._queue.pop(0), usage=Usage())

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        resp = await self.generate(messages, options=options)
        text = " ".join(b.text for b in resp.content if isinstance(b, TextBlock) and b.text)
        if text:
            yield TextDelta(text=text)
        yield CompletionEvent(content=resp.content, usage=resp.usage)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def run_agent(
    rt: Runtime,
    agent: ReActAgent,
    text: str,
    *,
    session_id: str | None = None,
) -> dict:
    """Submit one message and block until the run completes."""
    await rt.register(agent)
    sid = session_id or agent.id.key
    msg = Message(
        target=agent.id,
        sender=AgentId(type="proxy", key="test"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
        correlation_id=sid,
    )
    run_id = await rt.submit(agent.id, msg)

    status = "success"
    error = None
    async for entry in rt.event_log.tail(run_id):
        if entry.kind == "run.completed":
            break
        elif entry.kind == "run.failed":
            status = entry.payload.get("status", "error")
            error = entry.payload.get("error")
            break
        elif entry.kind == "run.cancelled":
            status = "cancelled"
            break

    output = ""
    history_msgs = await agent.history.get_messages(agent.id, session_id=sid)
    for m in reversed(history_msgs):
        if m.role == Role.ASSISTANT:
            output = " ".join(
                b.text for b in m.content if isinstance(b, TextBlock) and b.text
            )
            break
    if error and not output:
        output = error

    return {"status": status, "output": output, "run_id": run_id}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_load_session_memory_seeds_chat_messages_without_message_envelopes():
    """Cold-store seeding should append ChatMessage objects to history."""

    class CapturingHistory:
        def __init__(self) -> None:
            self.seen_message = None

        async def append(self, agent_id, message, *, session_id, run_id="") -> None:
            self.seen_message = message

        async def append_many(self, agent_id, messages, *, session_id, run_id="") -> None:
            self.seen_message = messages[0] if messages else None

        async def get_messages(self, agent_id, *, session_id, limit=None, offset=None):
            return [self.seen_message] if self.seen_message is not None else []

        async def clear(self, agent_id, *, session_id) -> None:
            self.seen_message = None

        async def clear_run(self, agent_id, *, session_id, run_id) -> None:
            self.seen_message = None

    history = CapturingHistory()

    async def fake_steps():
        return [
            {
                "type": "user_message",
                "input": "hello",
                "output": None,
                "metadata": {},
            }
        ]

    await load_session_memory(
        session_id="session-seed",
        system_instructions="system prompt",
        load_persisted_steps=fake_steps,
        history=history,
    )

    assert history.seen_message is not None
    assert isinstance(history.seen_message, ChatMessage)
    assert history.seen_message.role == "user"
    assert history.seen_message.content[0].text == "hello"


async def test_standalone_session_accumulates_across_runs():
    """History accumulates across multiple submissions with the same session_id."""
    shared_history = InMemoryHistoryProvider()
    async with Runtime() as rt:
        agent = ReActAgent(
            "bot",
            model=MockLLMClient(
                [
                    [TextBlock(text="I am fine.")],
                    [TextBlock(text="You said hi earlier.")],
                ]
            ),
            context=ContextConfig(shared_history, SlidingWindowCompaction(max_messages=20)),
            max_iterations=5,
        )

        r1 = await run_agent(rt, agent, "Hi!")
        assert r1["status"] == "success"

        r2 = await run_agent(rt, agent, "What did I say?")
        assert r2["status"] == "success"
        assert r2["output"] == "You said hi earlier."

        # 2 user + 2 assistant = 4 messages in the session
        msgs = await shared_history.get_messages(agent.id, session_id=agent.id.key)
        assert len(msgs) == 4


async def test_session_isolation_across_different_sessions():
    """Two sessions of the same agent don't bleed into each other."""
    shared_history = InMemoryHistoryProvider()
    async with Runtime() as rt:
        agent = ReActAgent(
            "agent",
            model=MockLLMClient(
                [
                    [TextBlock(text="Session A response.")],
                    [TextBlock(text="Session B response.")],
                ]
            ),
            context=ContextConfig(shared_history, SlidingWindowCompaction(max_messages=20)),
            max_iterations=5,
        )

        r_a = await run_agent(rt, agent, "Hello A.", session_id="session-A")
        assert r_a["status"] == "success"

        r_b = await run_agent(rt, agent, "Hello B.", session_id="session-B")
        assert r_b["status"] == "success"

        msgs_a = await shared_history.get_messages(agent.id, session_id="session-A")
        msgs_b = await shared_history.get_messages(agent.id, session_id="session-B")
        assert len(msgs_a) == 2
        assert len(msgs_b) == 2


async def test_cross_run_memory_same_session():
    """Agent sees prior turns when submitted with the same correlation_id."""
    shared_history = InMemoryHistoryProvider()
    async with Runtime() as rt:
        agent = ReActAgent(
            "mem-bot",
            model=MockLLMClient(
                [
                    [TextBlock(text="Stored.")],
                    [TextBlock(text="The answer was 42.")],
                ]
            ),
            context=ContextConfig(shared_history, SlidingWindowCompaction(max_messages=40)),
            max_iterations=3,
        )

        sid = "cross-run-session"
        r1 = await run_agent(rt, agent, "Remember 42.", session_id=sid)
        assert r1["status"] == "success"

        r2 = await run_agent(rt, agent, "What was the answer?", session_id=sid)
        assert r2["status"] == "success"
        assert "42" in r2["output"]

        all_msgs = await shared_history.get_messages(agent.id, session_id=sid)
        assert len(all_msgs) == 4  # 2 user + 2 assistant
