"""Redis-backed short-term memory store.

Stores messages as a JSON list inside a single Redis key per session.
Keys are automatically expired via TTL to prevent unbounded growth.

Design:
  - Fully async using ``redis.asyncio``.
  - Each session gets its own key: ``session:{session_id}:messages``
  - Session metadata stored at: ``session:{session_id}:meta``
  - All data is JSON-serialized through ``message_serializer``.

Security:
  - Session IDs are validated to prevent key injection.
  - TTL is enforced on every write to avoid orphaned data.
  - No raw ``eval`` / ``KEYS *`` patterns — uses explicit key names.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis

from agent_framework.messages.base_message import BaseClientMessage
from agent_framework.memory.message_serializer import (
    serialize_message,
    deserialize_message,
)

logger = logging.getLogger("agent_framework.memory.redis")

# Allowed characters for session IDs (UUIDs, alphanumeric, hyphens, underscores)
_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _validate_session_id(session_id: str) -> None:
    """Validate session ID to prevent Redis key injection."""
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must match {_SESSION_ID_PATTERN.pattern}"
        )


class RedisMemory:
    """Async Redis-backed short-term message store.

    Parameters:
        redis_url: Redis connection URL (``redis://host:port/db``).
        default_ttl: Default TTL in seconds for session keys (0 = no expiry).
        max_messages: Hard cap on messages per session (oldest are dropped).
        key_prefix: Prefix for all Redis keys (namespacing).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 3600,
        max_messages: int = 200,
        key_prefix: str = "agent_session",
    ):
        self._redis_url = redis_url
        self._default_ttl = default_ttl
        self._max_messages = max_messages
        self._key_prefix = key_prefix
        self._client: Optional[aioredis.Redis] = None

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Initialize the Redis connection pool."""
        if self._client is not None:
            return
        self._client = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            max_connections=20,
        )
        # Verify connectivity
        await self._client.ping()
        # Redact credentials from URL before logging
        parsed = urlparse(self._redis_url)
        safe_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}/{parsed.path.lstrip('/')}"
        logger.info("Redis memory connected to %s", safe_url)

    async def disconnect(self) -> None:
        """Close the Redis connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("Redis memory disconnected")

    def _ensure_connected(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError(
                "RedisMemory not connected. Call await connect() first."
            )
        return self._client

    # -- Key helpers ----------------------------------------------------------

    def _msg_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}:messages"

    def _meta_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}:meta"

    # -- Message operations ---------------------------------------------------

    async def add_message(
        self, session_id: str, message: BaseClientMessage
    ) -> None:
        """Append a message to the session's message list.

        Trims to ``max_messages`` and refreshes the TTL.
        """
        _validate_session_id(session_id)
        client = self._ensure_connected()
        key = self._msg_key(session_id)

        serialized = json.dumps(serialize_message(message), default=str)

        pipe = client.pipeline(transaction=True)
        pipe.rpush(key, serialized)
        # Trim to keep only the last max_messages entries
        if self._max_messages > 0:
            pipe.ltrim(key, -self._max_messages, -1)
        # Refresh TTL
        if self._default_ttl > 0:
            pipe.expire(key, self._default_ttl)
        await pipe.execute()

    async def add_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> None:
        """Append multiple messages in a single pipeline."""
        _validate_session_id(session_id)
        if not messages:
            return

        client = self._ensure_connected()
        key = self._msg_key(session_id)

        serialized_items = [
            json.dumps(serialize_message(m), default=str) for m in messages
        ]

        pipe = client.pipeline(transaction=True)
        pipe.rpush(key, *serialized_items)
        if self._max_messages > 0:
            pipe.ltrim(key, -self._max_messages, -1)
        if self._default_ttl > 0:
            pipe.expire(key, self._default_ttl)
        await pipe.execute()

    async def get_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        """Retrieve messages for a session.

        Args:
            session_id: Session identifier.
            limit: Return only the last N messages. ``None`` = all.
        """
        _validate_session_id(session_id)
        client = self._ensure_connected()
        key = self._msg_key(session_id)

        if limit is not None and limit > 0:
            raw_items = await client.lrange(key, -limit, -1)
        else:
            raw_items = await client.lrange(key, 0, -1)

        messages: List[BaseClientMessage] = []
        for raw in raw_items:
            data = json.loads(raw)
            messages.append(deserialize_message(data))
        return messages

    async def get_message_count(self, session_id: str) -> int:
        """Return the number of messages stored for a session."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        return await client.llen(self._msg_key(session_id))

    async def clear(self, session_id: str) -> None:
        """Delete all messages for a session."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        await client.delete(self._msg_key(session_id))

    async def exists(self, session_id: str) -> bool:
        """Check whether a session has messages in Redis."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        return await client.exists(self._msg_key(session_id)) > 0

    # -- Metadata operations --------------------------------------------------

    async def set_metadata(
        self, session_id: str, metadata: Dict[str, Any]
    ) -> None:
        """Store session metadata as a Redis hash."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        key = self._meta_key(session_id)

        # Flatten metadata to string values for HSET
        flat: Dict[str, str] = {}
        for k, v in metadata.items():
            flat[k] = json.dumps(v, default=str) if not isinstance(v, str) else v

        pipe = client.pipeline(transaction=True)
        if flat:
            pipe.hset(key, mapping=flat)
        if self._default_ttl > 0:
            pipe.expire(key, self._default_ttl)
        await pipe.execute()

    async def get_metadata(self, session_id: str) -> Dict[str, Any]:
        """Retrieve session metadata."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        raw = await client.hgetall(self._meta_key(session_id))

        result: Dict[str, Any] = {}
        for k, v in raw.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result

    # -- TTL management -------------------------------------------------------

    async def refresh_ttl(self, session_id: str, ttl: Optional[int] = None) -> None:
        """Reset the TTL on both message and metadata keys."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl <= 0:
            return

        pipe = client.pipeline(transaction=True)
        pipe.expire(self._msg_key(session_id), effective_ttl)
        pipe.expire(self._meta_key(session_id), effective_ttl)
        await pipe.execute()

    async def get_ttl(self, session_id: str) -> int:
        """Return remaining TTL in seconds for the message key (-1 = no expiry, -2 = key missing)."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        return await client.ttl(self._msg_key(session_id))

    # -- Cleanup --------------------------------------------------------------

    async def delete_session(self, session_id: str) -> None:
        """Remove all Redis keys for a session (messages + metadata)."""
        _validate_session_id(session_id)
        client = self._ensure_connected()
        await client.delete(
            self._msg_key(session_id),
            self._meta_key(session_id),
        )

    # -- Context manager ------------------------------------------------------

    async def __aenter__(self) -> "RedisMemory":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
