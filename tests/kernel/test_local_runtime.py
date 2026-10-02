"""``Runtime.local`` — the zero-infra durable runtime on a SQLite file.

The store contract itself (leases, journal appends, inbox, signals, supervision) is the
runtime-store conformance suite, run against SQLite in ``test_sqlite_runtime_store.py``.
What only this level can show is the whole stack together and durability across a
process lifetime.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from substrate.types import ChatMessage, Role, TextBlock
from substrate.types import Actor
from substrate.types import Usage
from substrate.models import ModelCapabilities
from substrate.runtime import ChatPayload, Message
from substrate.types import CompletionEvent
from substrate.types import RunLogKind
from substrate.runtime import Delivery
from substrate.agents import ReActAgent
from substrate.runtime import Runtime
from substrate.testing.runtime import runtime_store

TERMINAL = (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED, RunLogKind.RUN_CANCELLED)


class MockChatModel:
    model = "mock-model"
    capabilities = ModelCapabilities(model_id="mock-model")

    async def generate_stream(self, messages, *, options=None, ctx=None):
        yield CompletionEvent(
            content=[TextBlock(text="hello from local runtime")],
            usage=Usage(input_tokens=10, output_tokens=5),
        )


def _chat(agent: Actor, text: str) -> Message:
    return Message(
        target=agent,
        sender=Actor(type="proxy", key="user"),
        payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])),
    )


async def _terminal(rt: Runtime, run_id: str) -> str:
    async def watch() -> str:
        async for entry in rt.tail(run_id):
            if entry.kind in TERMINAL:
                return str(entry.kind)
        raise AssertionError("tail ended without a terminal entry")

    return await asyncio.wait_for(watch(), 10)


async def test_react_agent_runs_end_to_end_on_local_runtime(tmp_path: Path) -> None:
    agent = ReActAgent("LocalBot", model=MockChatModel(), max_iterations=3)

    async with Runtime.open(tmp_path / "rt.db") as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _chat(agent.id, "hi"))
        assert await _terminal(rt, run_id) == "run.completed"
        text = [e.payload["text"] for e in await rt.read(run_id) if e.kind == RunLogKind.ASSISTANT_MESSAGE]
        assert text == ["hello from local runtime"]


async def test_a_run_submitted_before_a_restart_is_picked_up_after_it(tmp_path: Path) -> None:
    """The property an in-memory runtime cannot offer: work outlives the process that
    accepted it. The run is created while no worker exists, then a fresh runtime on the
    same file runs it."""
    path = tmp_path / "rt.db"
    agent = ReActAgent("LocalBot", model=MockChatModel(), max_iterations=3)

    from substrate.runtime import RunSpec

    store = runtime_store(path)
    await store.start()
    run = await store.create_run(RunSpec(agent=agent.id), deliveries=[Delivery(agent=agent.id, msg=_chat(agent.id, "hi"))])
    await store.aclose()

    async with Runtime.open(path) as rt:
        await rt.register(agent)
        assert await _terminal(rt, run.run_id) == "run.completed"
