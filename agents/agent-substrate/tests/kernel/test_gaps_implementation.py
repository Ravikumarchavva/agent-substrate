from __future__ import annotations

import pytest

from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.stream import AgentProgress, AgentStep
from ravi.kernel.messaging.message import Message, ProgressPayload, _PAYLOAD_REGISTRY
from ravi.integrations.events.envelope import EventEnvelope


def test_progress_payload_pydantic_serialization():
    # Gap 6: AgentProgress is Pydantic and ProgressPayload serializes correctly
    agent_id = AgentId(type="assistant", key="test")
    progress = AgentProgress(
        agent_id=agent_id,
        step=AgentStep.THINKING,
        content="Thinking step",
        run_id="run-123",
        seq=42,
    )

    payload = ProgressPayload(progress=progress)
    assert payload.kind == "progress"

    # Assert it is registered in the payload types
    assert "progress" in _PAYLOAD_REGISTRY

    # Check serialization and deserialization
    msg = Message(
        target=TopicId(type="agent.progress", source="run-123"),
        payload=payload,
    )

    serialized = msg.model_dump_json()
    deserialized = Message.model_validate_json(serialized)

    assert isinstance(deserialized.payload, ProgressPayload)
    assert deserialized.payload.progress.seq == 42
    assert deserialized.payload.progress.content == "Thinking step"
    assert deserialized.payload.progress.agent_id.key == "test"


def test_event_envelope_translation():
    # Gap 4: EventEnvelope translation to/from kernel Event
    env = EventEnvelope(
        event_type="identity.user_created",
        payload={"username": "alice"},
        trace_context={
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        },
    )

    # To kernel event
    kernel_event = env.to_kernel_event()
    assert kernel_event.type == "identity.user_created"
    assert kernel_event.source == "serving"
    assert kernel_event.data["username"] == "alice"
    assert (
        kernel_event.data["_trace"]["traceparent"]
        == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )

    # From kernel event
    reconstructed = EventEnvelope.from_kernel_event(kernel_event)
    assert reconstructed.event_type == "identity.user_created"
    assert reconstructed.payload == {
        "username": "alice",
        "_trace": {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        },
    }
    assert reconstructed.event_id == env.event_id


@pytest.mark.asyncio
async def test_runtime_agent_registration():
    """Runtime: register + submit routes message to the agent inbox."""
    from ravi.agents.runtime import Runtime
    from ravi.kernel.core.identity import AgentId
    from ravi.kernel.messaging.message import Message, DataPayload

    received: list = []

    class EchoAgent:
        id = AgentId(type="agent", key="echo")
        model = None
        tools = None

        async def run(self, ctx, inbox):
            received.extend(inbox)

    agent = EchoAgent()
    async with Runtime() as rt:
        await rt.register(agent)
        msg = Message(
            target=agent.id,
            payload=DataPayload(data={"hello": "world"}),
        )
        await rt.submit(agent.id, msg)
        # Give the worker a tick to process
        import asyncio
        await asyncio.sleep(0.2)

    assert len(received) == 1
    assert received[0].payload.data == {"hello": "world"}
