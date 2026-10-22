"""The Redis session-state cache, held to the same suite as the store's own session state."""

from __future__ import annotations

import pytest

from loamz.engine.agents.providers.redis.short_term import RedisSessionStore
from loamz.engine.agents.testing.conformance.short_term_memory import ShortTermMemoryConformance

pytestmark = [pytest.mark.requires_redis]


class TestRedisSessionStore(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self, redis_url):
        store = RedisSessionStore(redis_url=redis_url, ttl=60)
        await store.connect()
        yield store
        await store.disconnect()
