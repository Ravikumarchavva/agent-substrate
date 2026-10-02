from __future__ import annotations

import pytest
from substrate.testing.runtime import ephemeral_runtime


@pytest.mark.asyncio
async def test_runtime_agent_registration():
    """Runtime: register + submit routes message to the agent inbox."""
    from substrate.types import Actor
    from substrate.runtime import Message, DataPayload

    received: list = []

    class EchoAgent:
        id = Actor(type="agent", key="echo")
        model = None
        tools = None

        async def run(self, ctx, inbox):
            received.extend(inbox)

    agent = EchoAgent()
    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            sender=Actor.system("test"),
            payload=DataPayload(data={"hello": "world"}),
        )
        await rt.submit(agent.id, msg)
        # Give the worker a tick to process
        import asyncio

        await asyncio.sleep(0.2)

    assert len(received) == 1
    assert received[0].payload.data == {"hello": "world"}
