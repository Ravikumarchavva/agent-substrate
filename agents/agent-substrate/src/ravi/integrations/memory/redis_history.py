"""RedisHistoryProvider — Redis-backed cached conversation history.

The default :class:`CachedHistoryProvider`.  Stores each session's messages as
a Redis list (``{prefix}:{session_id}:messages``) with a TTL and a hard
``max_messages`` cap enforced via ``LTRIM``.  Multi-session: every method takes
a ``session_id``.

Security: session IDs are validated to prevent Redis key injection; TTL is
enforced on every write.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis

from ravi.kernel.memory.history_provider import CachedHistoryProvider
from ravi.kernel.memory.message_serializer import (
    deserialize_message,
    serialize_message,
)
from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.logger import setup_logging

logger = setup_logging()

_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must match {_SESSION_ID_PATTERN.pattern}"
        )


class RedisHistoryProvider(CachedHistoryProvider):
    """Redis-backed :class:`CachedHistoryProvider`.

    Parameters
    ----------
    redis_url:
        Redis connection URL.
    ttl:
        TTL in seconds for session keys (0 = no expiry).
    max_messages:
        Hard cap per session — oldest messages are dropped when exceeded.
    key_prefix:
        Prefix for all Redis keys (namespacing).
    """

    def __init__(
        self,
        *,
        redis_url: str = "redis://localhost:6379/0",
        ttl: int = 3600,
        max_messages: int = 200,
        key_prefix: str = "agent_session",
    ) -> None:
        super().__init__(ttl=ttl, max_messages=max_messages)
        self._redis_url = redis_url
        self._key_prefix = key_prefix
        self._client: Optional[aioredis.Redis] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

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
        safe_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        logger.info("RedisHistoryProvider connected to %s", safe_url)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            logger.info("RedisHistoryProvider disconnected")
        self._client = None

    def _require_client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError(
                "RedisHistoryProvider not connected — call `await connect()` first."
            )
        return self._client

    def _msg_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}:messages"

    # ── HistoryProvider contract ──────────────────────────────────────────────

    async def save_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> int:
        _validate_session_id(session_id)
        if not messages:
            return 0
        client = self._require_client()
        key = self._msg_key(session_id)
        serialized = [
            json.dumps(serialize_message(m), default=str) for m in messages
        ]
        pipe = client.pipeline(transaction=True)
        pipe.rpush(key, *serialized)
        if self._max_messages > 0:
            pipe.ltrim(key, -self._max_messages, -1)
        if self._ttl > 0:
            pipe.expire(key, self._ttl)
        await pipe.execute()
        return len(messages)

    async def load_messages(
        self, session_id: str, *, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        _validate_session_id(session_id)
        client = self._require_client()
        key = self._msg_key(session_id)
        if limit is not None and limit > 0:
            raw_items = await client.lrange(key, -limit, -1)  # type: ignore[misc]
        else:
            raw_items = await client.lrange(key, 0, -1)  # type: ignore[misc]
        return [deserialize_message(json.loads(raw)) for raw in raw_items]

    async def count_messages(self, session_id: str) -> int:
        _validate_session_id(session_id)
        client = self._require_client()
        return await client.llen(self._msg_key(session_id))  # type: ignore[misc]

    async def clear_session(self, session_id: str) -> None:
        _validate_session_id(session_id)
        client = self._require_client()
        await client.delete(self._msg_key(session_id))

    # ── CachedHistoryProvider ─────────────────────────────────────────────────

    async def refresh_ttl(self, session_id: str) -> None:
        _validate_session_id(session_id)
        if self._ttl <= 0:
            return
        client = self._require_client()
        await client.expire(self._msg_key(session_id), self._ttl)

    def __repr__(self) -> str:
        return (
            f"<RedisHistoryProvider(ttl={self._ttl}, "
            f"max_messages={self._max_messages}, "
            f"connected={self._client is not None})>"
        )
