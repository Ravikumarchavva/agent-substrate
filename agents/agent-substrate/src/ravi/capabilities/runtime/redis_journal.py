"""RedisJournal — Stage 1 durable Journal backed by redis.asyncio.

Key format::

    ravi:journal:{effect_id}   →  JSON-encoded EffectResult

``record`` uses SET NX (set-if-not-exists) so the at-most-once guarantee
holds even under concurrent replay: the first writer wins and later writes
are silently ignored.  A TTL bounds storage cost for long-running deploys.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ravi.kernel.runtime.effects import EffectResult

if TYPE_CHECKING:
    import redis.asyncio as aioredis

_KEY_PREFIX = "ravi:journal:"


class RedisJournal:
    """Redis-backed Journal implementing the kernel Journal Protocol."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        ttl_seconds: int = 86400,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds

    async def lookup(self, effect_id: str) -> EffectResult | None:
        raw = await self._client.get(f"{_KEY_PREFIX}{effect_id}")
        if raw is None:
            return None
        data = json.loads(raw)
        return EffectResult.model_validate(data)

    async def record(self, result: EffectResult) -> None:
        key = f"{_KEY_PREFIX}{result.effect_id}"
        payload = result.model_dump_json()
        await self._client.set(key, payload, nx=True, ex=self._ttl)


__all__ = ["RedisJournal"]
