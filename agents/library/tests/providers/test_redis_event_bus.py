"""The Redis event bus held to the EventBus suite (needs Redis: LOAMZ_TEST_REDIS_URL)."""

from __future__ import annotations

import pytest

from loamz.agents.testing.conformance.event_bus import EventBusConformance

pytestmark = pytest.mark.requires_redis


class TestRedisEventBus(EventBusConformance):
    @pytest.fixture
    async def bus(self, redis_url):
        from loamz.agents.providers.redis.bus import RedisEventBus

        bus = RedisEventBus(redis_url)
        await bus.connect()
        yield bus
        await bus.disconnect()
