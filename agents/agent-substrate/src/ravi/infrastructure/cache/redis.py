"""RedisConnector — async Redis client with connect/disconnect lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ravi.logger import setup_logging

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = setup_logging()


class RedisConnector:
    """Thin lifecycle wrapper around ``redis.asyncio.Redis``.

    Parameters
    ----------
    url
        Redis connection URL (e.g. ``redis://localhost:6379/0``).
    decode_responses
        If True (default), all responses are decoded to str.
        Set to False for binary data (e.g. session stores that pickle values).
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        decode_responses: bool = True,
    ) -> None:
        self._url = url
        self._decode_responses = decode_responses
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Create the Redis client."""
        import redis.asyncio as _aioredis

        self._client = _aioredis.from_url(
            self._url, decode_responses=self._decode_responses
        )

    async def disconnect(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:
        """The underlying redis.asyncio client. Available after connect()."""
        if self._client is None:
            raise RuntimeError("RedisConnector not connected")
        return self._client

    # ── Common operations ────────────────────────────────────────────────────

    async def get(self, key: str) -> Any:
        return await self.client.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ex: int | None = None,
    ) -> None:
        await self.client.set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        return await self.client.delete(*keys)

    async def exists(self, *keys: str) -> int:
        return await self.client.exists(*keys)

    async def expire(self, key: str, seconds: int) -> bool:
        return await self.client.expire(key, seconds)

    async def publish(self, channel: str, message: str) -> int:
        return await self.client.publish(channel, message)

    async def keys(self, pattern: str = "*") -> list[Any]:
        return await self.client.keys(pattern)
