from __future__ import annotations

from tests._stores import fs_history

from substrate.models import ModelCapabilities
from typing import AsyncIterator
import pytest

from substrate.context import ContextConfig
from substrate.context import DefaultCompactionCoordinator
from substrate.context import ThresholdCheckpointStrategy
from substrate.agents import ReActAgent
from substrate.runtime import Runtime
from substrate.types import ChatMessage, ContentBlock, Role, TextBlock
from substrate.types import Actor
from substrate.models.protocols import CompletionEvent, TextDelta, Usage
from substrate.models import GenerationOptions, LLMResponse
from substrate.runtime import ChatPayload, Message
from substrate.testing.runtime import ephemeral_runtime


class MockChatModel:
    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._queue = list(responses)
        self.model = "mock-model"
        self.capabilities = ModelCapabilities(model_id="mock-model")

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        assert self._queue, "MockChatModel: no more scripted responses"
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
        ctx: object = None,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)


async def wait_run(rt: Runtime, run_id: str) -> None:
    async for entry in rt.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            assert entry.kind == "run.completed", f"Run ended with {entry.kind}: {entry.payload}"
            break


@pytest.mark.asyncio
async def test_react_agent_post_turn_compaction_creates_checkpoint():
    history = fs_history()
    coordinator = DefaultCompactionCoordinator(
        summarizer=ThresholdCheckpointStrategy(turn_threshold=2)
    )
    ctx_cfg = ContextConfig(history=history, coordinator=coordinator)

    llm = MockChatModel([
        [TextBlock(text="Response to turn 1")],
        [TextBlock(text="Response to turn 2")],
    ])

    user = Actor(type="user", key="bob")
    agent = ReActAgent("compact-agent", model=llm, context=ctx_cfg)
    session_id = "test-compaction-sess"

    async with ephemeral_runtime() as rt:
        await rt.register(agent)

        # Turn 1: 1 user message + 1 assistant message = 2 messages -> triggers turn_threshold=2!
        msg1 = Message(
            sender=user,
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="Turn 1 prompt")],
                )
            ),
            correlation_id=session_id,
        )
        run_id_1 = await rt.submit(agent.id, msg1)
        await wait_run(rt, run_id_1)

        # Verify a checkpoint was automatically saved
        checkpoints = await history.list_checkpoints(session_id)
        assert len(checkpoints) == 1
        cp = checkpoints[0]
        assert cp.session_id == session_id

        branch = await history.get_branch(session_id, "main")
        assert branch is not None
        assert cp.anchor_message_id == branch.head_message_id
        assert "Auto-generated checkpoint" in cp.summary

        # Turn 2: Verify that history builds seamlessly with checkpoint summary spliced
        msg2 = Message(
            sender=user,
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="Turn 2 prompt")],
                )
            ),
            correlation_id=session_id,
        )
        run_id_2 = await rt.submit(agent.id, msg2)
        await wait_run(rt, run_id_2)

        # Branch head advanced beyond the checkpoint anchor
        branch_after = await history.get_branch(session_id, "main")
        assert branch_after is not None
        assert branch_after.head_message_id != cp.anchor_message_id

