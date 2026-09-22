from __future__ import annotations

from typing import AsyncIterator
import pytest

from substrate.agents.context import ContextConfig
from substrate.agents.storage.history import InMemoryHistoryProvider
from substrate.agents.core.react import ReActAgent
from substrate.agents.runtime.runtime import Runtime
from substrate.kernel.core.content import ChatMessage, ContentBlock, Role, TextBlock
from substrate.kernel.core.identity import Actor
from substrate.kernel.llm.llm import (
    CompletionEvent,
    GenerationOptions,
    LLMResponse,
    TextDelta,
    Usage,
)
from substrate.kernel.messaging.message import ChatPayload, Message


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

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        resp = await self.generate(messages, options=options)
        text = " ".join(
            b.text for b in resp.content if isinstance(b, TextBlock) and b.text
        )
        if text:
            yield TextDelta(text=text)
        yield CompletionEvent(content=resp.content, usage=resp.usage)

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)


async def wait_run(rt: Runtime, run_id: str) -> None:
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            assert entry.kind == "run.completed", f"Run ended with {entry.kind}: {entry.payload}"
            break


@pytest.mark.asyncio
async def test_react_agent_branching_and_dag_history():
    history = InMemoryHistoryProvider()
    ctx_cfg = ContextConfig(history=history)

    llm = MockLLMClient([
        [TextBlock(text="Hello Alice! Pleased to meet you.")],
        [TextBlock(text="Your name is Alice, and this is an experiment branch.")],
        [TextBlock(text="Your name is Alice on the main branch.")],
    ])

    user = Actor(type="user", key="alice")
    agent = ReActAgent("assistant", model=llm, context=ctx_cfg)
    session_id = "test-session-branching"

    async with Runtime() as rt:
        await rt.register(agent)

        # Turn 1: On main branch
        msg1 = Message(
            sender=user,
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="Hello, my name is Alice.")],
                )
            ),
            correlation_id=session_id,
            metadata={"branch_id": "main"},
        )
        run_id_1 = await rt.submit(agent.id, msg1)
        await wait_run(rt, run_id_1)

        # Verify DAG nodes on main branch
        main_branch = await history.get_branch(session_id, "main")
        assert main_branch is not None
        assert main_branch.head_message_id is not None
        assert main_branch.version >= 1

        main_head_after_turn1 = main_branch.head_message_id

        # Fork to 'experiment' branch
        forked_b = await history.fork_branch(session_id, "main", "experiment")
        assert forked_b.head_message_id == main_head_after_turn1

        # Turn 2: Run on 'experiment' branch
        msg2 = Message(
            sender=user,
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="What is my name and branch?")],
                )
            ),
            correlation_id=session_id,
            metadata={"branch_id": "experiment"},
        )
        run_id_2 = await rt.submit(agent.id, msg2)
        await wait_run(rt, run_id_2)

        # Verify experiment branch advanced
        exp_branch = await history.get_branch(session_id, "experiment")
        assert exp_branch is not None
        assert exp_branch.head_message_id != main_head_after_turn1

        # Verify main branch head was untouched
        main_branch_after = await history.get_branch(session_id, "main")
        assert main_branch_after is not None
        assert main_branch_after.head_message_id == main_head_after_turn1

        # Turn 3: Run on 'main' branch
        msg3 = Message(
            sender=user,
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="What is my name on main?")],
                )
            ),
            correlation_id=session_id,
            metadata={"branch_id": "main"},
        )
        run_id_3 = await rt.submit(agent.id, msg3)
        await wait_run(rt, run_id_3)

        # Now main branch advanced independently
        main_branch_final = await history.get_branch(session_id, "main")
        assert main_branch_final is not None
        assert main_branch_final.head_message_id != main_head_after_turn1
        assert main_branch_final.head_message_id != exp_branch.head_message_id
