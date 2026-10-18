"""The Redis session-state cache, held to the same suite as the store's own session state."""

from __future__ import annotations

import pytest

from substrate.integrations.memory import RedisSessionStore
from substrate.testing.conformance.short_term_memory import ShortTermMemoryConformance

pytestmark = [pytest.mark.requires_redis]


class TestRedisSessionStore(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self):
        store = RedisSessionStore(redis_url="redis://localhost:6379/0", ttl=60)
        await store.connect()
        yield store
        await store.disconnect()
