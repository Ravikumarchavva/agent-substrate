"""RedisSessionStore — Redis-backed ShortTermMemory.

Stores session state as a Redis HASH (one HASH per session_id).
Each field in the HASH is a JSON-serialized value so any JSON-compatible
type is supported.  An optional TTL automatically expires idle sessions.

Usage::

    store = RedisSessionStore(redis_url="redis://localhost:6379/0", ttl=3600)
    await store.connect()

    await store.update_state("sess-123", {"preferred_language": "Python"})
    state = await store.get_state("sess-123")
    # {"preferred_language": "Python"}

    await store.disconnect()
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from agent_substrate.logger import setup_logging

logger = setup_logging()

_KEY_PREFIX = "session:state:"


class RedisSessionStore:
    """ShortTermMemory backed by Redis HASHes.

    Each session maps to a Redis HASH at key ``session:state:<session_id>``.
    Fields are stored as JSON so any JSON-serializable value is supported.

    Parameters
    ----------
    redis_url:
        Redis connection URL.
    ttl:
        Seconds before an idle session key expires.  ``None`` = no expiry.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        *,
        ttl: int | None = 3600,
    ) -> None:
        self._url = redis_url
        self._ttl = ttl
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = aioredis.from_url(self._url, decode_responses=False)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    def _key(self, session_id: str) -> str:
        return f"{_KEY_PREFIX}{session_id}"

    def _r(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError(
                "RedisSessionStore not connected — call await connect() first"
            )
        return self._redis

    async def get_state(self, session_id: str) -> dict[str, Any]:
        raw = await self._r().hgetall(self._key(session_id))
        return {k.decode(): json.loads(v) for k, v in raw.items()}

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        key = self._key(session_id)
        pipe = self._r().pipeline()
        pipe.delete(key)
        if state:
            pipe.hset(key, mapping={k: json.dumps(v) for k, v in state.items()})
            if self._ttl:
                pipe.expire(key, self._ttl)
        await pipe.execute()

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        if not patch:
            return
        key = self._key(session_id)
        pipe = self._r().pipeline()
        pipe.hset(key, mapping={k: json.dumps(v) for k, v in patch.items()})
        if self._ttl:
            pipe.expire(key, self._ttl)
        await pipe.execute()

    async def clear(self, session_id: str) -> None:
        await self._r().delete(self._key(session_id))

    async def __aenter__(self) -> RedisSessionStore:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


__all__ = ["RedisSessionStore"]
