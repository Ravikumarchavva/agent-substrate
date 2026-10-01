"""The routed agent: handlers declared by payload type, dispatch owned by the base."""

from __future__ import annotations

import asyncio

import pytest

from substrate.kernel.abstractions.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.exceptions import UnroutableMessageError
from substrate.kernel.abstractions.messaging.message import ChatPayload, DataPayload, Message
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.abstractions.runtime.store import Delivery
from substrate.kernel.agents.routed import RoutedAgent, handle
from substrate.kernel.runtime import Runtime
from substrate.kernel.runtime.context import RunContext

ME = Actor("agent", "routed")


def _chat(text: str) -> Message:
    return Message(target=ME, sender=Actor.system("t"), payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])))


def _data(**data: object) -> Message:
    return Message(target=ME, sender=Actor.system("t"), payload=DataPayload(data=dict(data)))


class Recorder(RoutedAgent):
    def __init__(self) -> None:
        self.id = ME
        self.seen: list[str] = []

    @handle(ChatPayload)
    async def on_chat(self, ctx: RunContext, msg: Message) -> None:
        self.seen.append(f"chat:{msg.payload.message.content[0].text}")

    @handle(DataPayload)
    async def on_data(self, ctx: RunContext, msg: Message) -> None:
        self.seen.append(f"data:{msg.payload.data}")


class OnlyChat(RoutedAgent):
    def __init__(self) -> None:
        self.id = ME

    @handle(ChatPayload)
    async def on_chat(self, ctx: RunContext, msg: Message) -> None:
        return None


async def _terminal(rt: Runtime, run_id: str) -> tuple[str, dict]:
    async def watch() -> tuple[str, dict]:
        async for entry in rt.tail(run_id):
            if entry.kind in (RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED, RunLogKind.RUN_CANCELLED):
                return str(entry.kind), dict(entry.payload or {})
        raise AssertionError("no terminal entry")

    return await asyncio.wait_for(watch(), 10)


async def test_each_message_goes_to_the_handler_for_its_payload_type_in_order() -> None:
    agent = Recorder()
    async with Runtime.local(":memory:") as rt:
        await rt.register(agent)
        run_id = await rt.submit(ME, _chat("hello"))
        await rt.store.deliver(Delivery(agent=ME, msg=_data(n=1)))
        kind, _ = await _terminal(rt, run_id)
        for _ in range(200):
            if len(agent.seen) == 2:
                break
            await asyncio.sleep(0.01)
    assert kind == RunLogKind.RUN_COMPLETED
    assert agent.seen == ["chat:hello", "data:{'n': 1}"]


async def test_a_subclass_inherits_handlers_and_can_override_one() -> None:
    class Loud(Recorder):
        @handle(ChatPayload)
        async def shout(self, ctx: RunContext, msg: Message) -> None:
            self.seen.append("LOUD")

    agent = Loud()
    async with Runtime.local(":memory:") as rt:
        await rt.register(agent)
        await _terminal(rt, await rt.submit(ME, _chat("x")))
    assert agent.seen == ["LOUD"], "the subclass's handler for ChatPayload replaces the parent's"
    assert Loud._routes[DataPayload] == "on_data", "handlers the subclass did not override are inherited"


async def test_an_unroutable_payload_fails_the_run_with_a_typed_reason_and_is_not_retried() -> None:
    agent = OnlyChat()
    async with Runtime.local(":memory:") as rt:
        await rt.register(agent)
        run_id = await rt.submit(ME, _data(n=1), max_retries=5)
        kind, payload = await _terminal(rt, run_id)
        events = await rt.read(run_id)

    assert kind == RunLogKind.RUN_FAILED
    assert "DataPayload" in str(payload.get("error")) and "ChatPayload" in str(payload.get("error")), payload
    assert not [e for e in events if e.kind == RunLogKind.RUN_RETRYING], "a poison message must not be retried"


def test_the_error_names_what_the_agent_accepts() -> None:
    agent = OnlyChat()
    with pytest.raises(UnroutableMessageError) as raised:
        agent._handler_for(_data(n=1))
    assert raised.value.accepts == ("ChatPayload",) and raised.value.payload_type == "DataPayload"


def test_handle_needs_a_payload_type() -> None:
    with pytest.raises(TypeError):
        handle()
