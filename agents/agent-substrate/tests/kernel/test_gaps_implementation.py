from __future__ import annotations

import pytest
import json
from datetime import datetime, timezone

from ravi.kernel.identity import AgentId, TopicId
from ravi.kernel.stream import AgentProgress, AgentStep, TextDelta
from ravi.kernel.message import Message, ProgressPayload, Payload, _PAYLOAD_REGISTRY
from ravi.serving.shared.events.envelope import EventEnvelope
from ravi.kernel.events import Event
from ravi.agents.runtime.local import LocalRuntime
from ravi.kernel.agent import Agent
from ravi.kernel.content import TextBlock


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
        trace_context={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
    )
    
    # To kernel event
    kernel_event = env.to_kernel_event()
    assert kernel_event.type == "identity.user_created"
    assert kernel_event.source == "serving"
    assert kernel_event.data["username"] == "alice"
    assert kernel_event.data["_trace"]["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    
    # From kernel event
    reconstructed = EventEnvelope.from_kernel_event(kernel_event)
    assert reconstructed.event_type == "identity.user_created"
    assert reconstructed.payload == {"username": "alice", "_trace": {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}}
    assert reconstructed.event_id == env.event_id


class DummyAgent:
    def __init__(self, agent_id: AgentId):
        self.id = agent_id
        self.runtime = None
        
    async def bind(self, runtime):
        self.runtime = runtime
        
    async def on_message(self, ctx, payload):
        return "reply"
        
    async def save_state(self):
        return {"state": 1}
        
    async def load_state(self, state):
        pass


@pytest.mark.asyncio
async def test_local_runtime_agent_registration():
    # Gap 5: register_agent, subscribe returning Subscription, unsubscribe taking Subscription
    async with LocalRuntime() as rt:
        agent_id = AgentId(type="assistant", key="dummy")
        agent = DummyAgent(agent_id)
        
        # Test register_agent
        await rt.register_agent(agent)
        assert agent.runtime is rt
        
        # Test subscribe returning Subscription
        topic = TopicId(type="agent.progress", source="test-run")
        sub = await rt.subscribe(agent_id, topic)
        
        assert sub.agent_id == agent_id
        assert sub.topic == topic
        assert len(rt._topic_subs[f"{topic.type}/{topic.source}"]) == 1
        
        # Test unsubscribe taking Subscription
        await rt.unsubscribe(sub)
        assert len(rt._topic_subs[f"{topic.type}/{topic.source}"]) == 0
