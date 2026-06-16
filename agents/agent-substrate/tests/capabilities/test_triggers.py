"""Tests for trigger capabilities (scheduler, webhooks, conditions) via native Runtime."""

from __future__ import annotations

import asyncio
import pytest

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message, DataPayload
from ravi.capabilities.triggers.scheduler import TriggerScheduler, TriggerDef
from ravi.capabilities.triggers.webhooks import WebhookRegistry
from ravi.capabilities.triggers.conditions import ConditionMonitor, ConditionDef
from ravi.integrations.events.redis_event_bus import EventBus
from ravi.integrations.events.envelope import EventEnvelope


class MockRuntime:
    def __init__(self) -> None:
        self.submitted: list[tuple[AgentId, Message]] = []
        self.submit_event = asyncio.Event()

    async def submit(self, agent_id: AgentId, msg: Message) -> str:
        self.submitted.append((agent_id, msg))
        self.submit_event.set()
        return "run-123"


@pytest.mark.asyncio
async def test_scheduler_trigger_dispatch():
    rt = MockRuntime()
    scheduler = TriggerScheduler(runtime=rt)
    await scheduler.start()

    trigger = TriggerDef(
        name="test-trigger",
        kind="interval",
        schedule="1",  # 1 second
        target_type="pipeline",
        target_name="test-pipeline",
        target_params={"param1": "val1"},
    )
    await scheduler.add_trigger(trigger)
    
    # Let's fire the trigger manually to avoid waiting for interval
    await scheduler._fire_trigger("test-trigger")

    assert len(rt.submitted) == 1
    agent_id, msg = rt.submitted[0]
    assert agent_id == AgentId(type="pipeline", key="test-pipeline")
    assert isinstance(msg.payload, DataPayload)
    assert msg.payload.data == {"param1": "val1"}

    await scheduler.stop()


@pytest.mark.asyncio
async def test_webhook_trigger_dispatch():
    rt = MockRuntime()
    registry = WebhookRegistry(runtime=rt)

    webhook = await registry.register(
        name="test-webhook",
        path="notify",
        target_type="chain",
        target_name="test-chain",
        target_params={"fixed": "data"},
    )

    res = await registry.handle(
        path="notify",
        payload={"dynamic": "input"},
        secret=webhook.secret,
    )

    assert res["status"] == "triggered"
    assert res["dispatched"] is True
    assert res["run_id"] == "run-123"

    assert len(rt.submitted) == 1
    agent_id, msg = rt.submitted[0]
    assert agent_id == AgentId(type="chain", key="test-chain")
    assert msg.payload.data == {"fixed": "data", "dynamic": "input"}


async def _redis_reachable(url: str) -> bool:
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(url)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_condition_trigger_dispatch(redis_url):
    if not await _redis_reachable(redis_url):
        pytest.skip("Redis not reachable")

    rt = MockRuntime()
    monitor = ConditionMonitor(runtime=rt)
    
    bus = EventBus(redis_url=redis_url)
    monitor.set_event_bus(bus)

    condition = ConditionDef(
        name="test-condition",
        event_type="user.created",
        filters={"role": "admin"},
        target_type="pipeline",
        target_name="on-admin-created",
        target_params={"action": "setup"},
    )
    await monitor.add_condition(condition)
    await monitor.start()

    # Publish matching event
    envelope = EventEnvelope(
        event_type="user.created",
        payload={"role": "admin", "username": "alice"},
    )
    await bus.publish(envelope)

    # Wait for dispatch
    try:
        await asyncio.wait_for(rt.submit_event.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        pass

    assert len(rt.submitted) == 1
    agent_id, msg = rt.submitted[0]
    assert agent_id == AgentId(type="pipeline", key="on-admin-created")
    # Check that event data was merged
    assert msg.payload.data["action"] == "setup"
    assert msg.payload.data["event"]["type"] == "user.created"
    assert msg.payload.data["event"]["data"] == {"role": "admin", "username": "alice"}

    await monitor.stop()
