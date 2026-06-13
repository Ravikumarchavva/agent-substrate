"""RedisHistoryProvider — Redis-backed HistoryProvider.

Stores each agent's ChatMessage log as a Redis list with TTL and a hard
max_messages cap (LTRIM on every write).  Each entry is a JSON object:
``{"run_id": "<str>", "msg": <ChatMessage.model_dump(mode="json")>}``.

Usage::

    provider = RedisHistoryProvider(redis_url="redis://localhost:6379/0")
    await provider.connect()
    # Pass to ReActAgent via context=ContextConfig(history_provider=provider)
    await provider.disconnect()
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis

from ravi.kernel import AgentId
from ravi.kernel.core.content import ChatMessage
from ravi.logger import setup_logging

logger = setup_logging()


def _serialize(message: ChatMessage, run_id: str) -> str:
    return json.dumps({"run_id": run_id, "msg": message.model_dump(mode="json")})


def _deserialize(raw: str) -> tuple[str, ChatMessage]:
    data = json.loads(raw)
    run_id: str = data.get("run_id", "")
    msg = ChatMessage.model_validate(data["msg"])
    return run_id, msg


def _tag(message: ChatMessage, run_id: str) -> ChatMessage:
    if not run_id or message.metadata.get("run_id") == run_id:
        return message
    return message.model_copy(
        update={"metadata": {**message.metadata, "run_id": run_id}}
    )


class RedisHistoryProvider:
    """Redis-backed :class:`HistoryProvider`.

    Parameters
    ----------
    redis_url:
        Redis connection URL (default: ``redis://localhost:6379/0``).
    ttl:
        Key TTL in seconds; 0 = no expiry.
    max_messages:
        Hard cap per agent — oldest messages are dropped when exceeded.
    key_prefix:
        Namespace prefix for all Redis keys.
    """

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        ttl: int = 3600,
        max_messages: int = 200,
        key_prefix: str = "ravi:hist",
    ) -> None:
        self._ttl = ttl
        self._max_messages = max_messages
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._client: Optional[aioredis.Redis] = None

    # -- lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            max_connections=20,
        )
        await self._client.ping()  # type: ignore[misc]
        parsed = urlparse(self._redis_url)
        logger.info(
            "RedisHistoryProvider connected: %s://%s:%s",
            parsed.scheme,
            parsed.hostname,
            parsed.port,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            logger.info("RedisHistoryProvider disconnected")
        self._client = None

    async def __aenter__(self) -> RedisHistoryProvider:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    def _require_client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError(
                "RedisHistoryProvider not connected — call `await connect()` first."
            )
        return self._client

    def _key(self, agent_id: AgentId, session_id: str) -> str:
        return f"{self._key_prefix}:{agent_id.type}:{agent_id.key}:{session_id}"

    # -- HistoryProvider protocol ---------------------------------------------

    async def append(
        self,
        agent_id: AgentId,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        client = self._require_client()
        key = self._key(agent_id, session_id)
        tagged = _tag(message, run_id)
        pipe = client.pipeline(transaction=True)
        pipe.rpush(key, _serialize(tagged, run_id))
        if self._max_messages > 0:
            pipe.ltrim(key, -self._max_messages, -1)
        if self._ttl > 0:
            pipe.expire(key, self._ttl)
        await pipe.execute()

    async def append_many(
        self,
        agent_id: AgentId,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        for message in messages:
            await self.append(agent_id, message, session_id=session_id, run_id=run_id)

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        client = self._require_client()
        key = self._key(agent_id, session_id)
        start = offset if offset is not None else 0
        end = (start + limit - 1) if limit is not None else -1
        raw_items: list[str] = await client.lrange(key, start, end)  # type: ignore[misc]
        return [_deserialize(r)[1] for r in raw_items]

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        client = self._require_client()
        await client.delete(self._key(agent_id, session_id))

    async def clear_run(
        self, agent_id: AgentId, *, session_id: str, run_id: str
    ) -> None:
        client = self._require_client()
        key = self._key(agent_id, session_id)
        all_raw: list[str] = await client.lrange(key, 0, -1)  # type: ignore[misc]
        kept = [r for r in all_raw if json.loads(r).get("run_id") != run_id]
        pipe = client.pipeline(transaction=True)
        pipe.delete(key)
        if kept:
            pipe.rpush(key, *kept)
            if self._ttl > 0:
                pipe.expire(key, self._ttl)
        await pipe.execute()

    async def count_messages(self, agent_id: AgentId, *, session_id: str) -> int:
        client = self._require_client()
        return await client.llen(self._key(agent_id, session_id))  # type: ignore[misc]

    async def refresh_ttl(self, agent_id: AgentId, *, session_id: str) -> None:
        if self._ttl <= 0:
            return
        client = self._require_client()
        await client.expire(self._key(agent_id, session_id), self._ttl)

    def __repr__(self) -> str:
        return (
            f"<RedisHistoryProvider("
            f"ttl={self._ttl}, max={self._max_messages}, "
            f"connected={self._client is not None})>"
        )
