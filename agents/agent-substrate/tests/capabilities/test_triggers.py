"""Tests for trigger capabilities (scheduler, webhooks, conditions) via native Runtime."""

from __future__ import annotations

import asyncio
import pytest

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message, DataPayload
from substrate.capabilities.triggers.scheduler import TriggerScheduler, TriggerDef
from substrate.capabilities.triggers.webhooks import WebhookRegistry
from substrate.capabilities.triggers.conditions import ConditionMonitor, ConditionDef
from substrate.integrations.events.redis_event_bus import EventBus
from substrate.integrations.events.envelope import EventEnvelope


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


def _sign(secret: str, raw_body: bytes) -> str:
    import hashlib
    import hmac

    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_trigger_dispatch():
    import json

    rt = MockRuntime()
    registry = WebhookRegistry(runtime=rt)

    webhook = await registry.register(
        name="test-webhook",
        path="notify",
        target_type="chain",
        target_name="test-chain",
        target_params={"fixed": "data"},
    )

    payload = {"dynamic": "input"}
    raw_body = json.dumps(payload).encode()

    res = await registry.handle(
        path="notify",
        payload=payload,
        raw_body=raw_body,
        signature=_sign(webhook.secret, raw_body),
    )

    assert res["status"] == "triggered"
    assert res["dispatched"] is True
    assert res["run_id"] == "run-123"

    assert len(rt.submitted) == 1
    agent_id, msg = rt.submitted[0]
    assert agent_id == AgentId(type="chain", key="test-chain")
    assert msg.payload.data == {"fixed": "data", "dynamic": "input"}


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature():
    rt = MockRuntime()
    registry = WebhookRegistry(runtime=rt)
    await registry.register(
        name="test-webhook", path="notify", target_type="chain", target_name="test-chain"
    )

    res = await registry.handle(
        path="notify", payload={}, raw_body=b"{}", signature="sha256=deadbeef"
    )

    assert res["dispatched"] is False
    assert "signature" in res["error"].lower()
    assert len(rt.submitted) == 0


@pytest.mark.asyncio
async def test_webhook_idempotency_key_dedupes_retried_delivery():
    import json

    rt = MockRuntime()
    registry = WebhookRegistry(runtime=rt)
    webhook = await registry.register(
        name="test-webhook", path="notify", target_type="chain", target_name="test-chain"
    )
    payload = {"dynamic": "input"}
    raw_body = json.dumps(payload).encode()
    signature = _sign(webhook.secret, raw_body)

    first = await registry.handle(
        path="notify",
        payload=payload,
        raw_body=raw_body,
        signature=signature,
        idempotency_key="delivery-1",
    )
    second = await registry.handle(
        path="notify",
        payload=payload,
        raw_body=raw_body,
        signature=signature,
        idempotency_key="delivery-1",
    )

    assert first == second
    assert len(rt.submitted) == 1


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
    await asyncio.sleep(0.2)  # Allow background subscription task to connect to Redis

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
