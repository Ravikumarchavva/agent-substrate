"""Redis-backed memory store — both per-session (Memory ABC) and
multi-session (low-level) APIs in one class.

Per-session API (Memory ABC)
-----------------------------
Pass ``session_id`` at construction time.  The instance maintains a local
in-process list that mirrors Redis, so ``get_messages()`` is O(1) with no
network hop.  Call ``restore()`` once at session start to reload prior history
from Redis.

    mem = RedisMemory(session_id="conv-abc", redis_url=REDIS_URL)
    async with mem:
        await mem.restore()           # reload prior history
        await mem.add_message(msg)    # writes local + Redis
        msgs = await mem.get_messages()   # reads local cache
        meta = await mem.get_metadata()   # reads Redis hash

Multi-session API (for SessionManager)
---------------------------------------
When the same ``RedisMemory`` instance manages many sessions, use the
explicit low-level methods that accept ``session_id`` per call:
``store()``, ``store_many()``, ``fetch()``, ``count()``, ``drop()``.

Security:
  - Session IDs are validated to prevent Redis key injection.
  - TTL is enforced on every write.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis

from agent_framework.core.memory.base_memory import BaseMemory
from agent_framework.core.memory.message_serializer import (
    deserialize_message,
    serialize_message,
)
from agent_framework.core.messages.base_message import BaseClientMessage

logger = logging.getLogger("agent_framework.core.memory.redis")

_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must match {_SESSION_ID_PATTERN.pattern}"
        )


class RedisMemory(BaseMemory):
    """Redis-backed memory — implements the Memory ABC when ``session_id``
    is supplied at construction, and exposes low-level multi-session helpers
    for ``SessionManager``.

    Parameters
    ----------
    session_id:
        When provided, enables the Memory ABC methods (``add_message``,
        ``get_messages``, ``clear``, ``restore``).  Required for agent use.
    redis_url:
        Redis connection URL.
    default_ttl:
        TTL in seconds for session keys (0 = no expiry).
    max_messages:
        Hard cap per session — oldest messages are dropped when exceeded.
    key_prefix:
        Prefix for all Redis keys (namespacing).
    auto_checkpoint_every:
        When > 0 and ``session_manager`` is set, checkpoint to Postgres
        every N new messages written.
    session_manager:
        Optional ``SessionManager`` for Postgres durability.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        redis_url: str = "redis://localhost:6379/0",
        default_ttl: int = 3600,
        max_messages: int = 200,
        key_prefix: str = "agent_session",
        auto_checkpoint_every: int = 50,
        session_manager: Optional[object] = None,
    ) -> None:
        self._session_id = session_id
        self._redis_url = redis_url
        self._default_ttl = default_ttl
        self._max_messages = max_messages
        self._key_prefix = key_prefix
        self._auto_checkpoint_every = auto_checkpoint_every
        self._session_manager = session_manager
        self._client: Optional[aioredis.Redis] = None
        # Local cache — populated by restore() and updated by add_message()
        self._messages: List[BaseClientMessage] = []
        self._write_count: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the Redis connection pool."""
        if self._client is not None:
            return
        self._client = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
            max_connections=20,
        )
        await self._client.ping()
        parsed = urlparse(self._redis_url)
        safe_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        logger.info("RedisMemory connected to %s", safe_url)

    @classmethod
    def for_session(cls, parent: "RedisMemory", session_id: str) -> "RedisMemory":
        """Create a session-bound clone that shares the parent's connection pool.

        The returned instance reuses the parent's Redis client (connection pool)
        so no new TCP connections are opened.  Lifecycle of the underlying pool
        remains with the *parent* — callers must **not** call ``disconnect()``
        on the returned instance.

        Example::

            shared = app.state.redis_memory          # connectionless session_id=None
            per_req = RedisMemory.for_session(shared, session_id="thread-42")
            await per_req.restore()                    # loads history from Redis
            agent = ReActAgent(..., memory=per_req)    # pass as agent memory
        """
        instance = cls(
            session_id=session_id,
            redis_url=parent._redis_url,
            default_ttl=parent._default_ttl,
            max_messages=parent._max_messages,
            key_prefix=parent._key_prefix,
            auto_checkpoint_every=parent._auto_checkpoint_every,
            session_manager=parent._session_manager,
        )
        # Share the existing connection pool — no new TCP connection
        instance._client = parent._client
        instance._owns_client = False
        return instance

    async def disconnect(self) -> None:
        """Close the Redis connection pool (only if this instance owns it)."""
        if self._client is not None and getattr(self, "_owns_client", True):
            await self._client.aclose()
            logger.info("RedisMemory disconnected")
        self._client = None

    def _require_client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError(
                "RedisMemory not connected — call `await connect()` first."
            )
        return self._client

    def _require_session_id(self) -> str:
        if self._session_id is None:
            raise RuntimeError(
                "This RedisMemory has no session_id.  Pass session_id= at "
                "construction time to use the Memory ABC methods."
            )
        return self._session_id

    # ── Key helpers ───────────────────────────────────────────────────────────

    def _msg_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}:messages"

    def _meta_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}:meta"

    # ── Memory ABC (per-session, session_id baked in at construction) ─────────

    async def add_message(self, message: BaseClientMessage) -> None:
        """Append *message* to local cache and write to Redis."""
        sid = self._require_session_id()
        self._messages.append(message)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_one(sid, message))
        except RuntimeError:
            logger.debug(
                "RedisMemory: no running loop for add_message, "
                "Redis write skipped for session %s",
                sid,
            )

    async def get_messages(
        self, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        """Return messages from the local cache (no Redis round-trip)."""
        self._require_session_id()
        msgs = self._messages
        if limit is not None and limit > 0:
            msgs = msgs[-limit:]
        return list(msgs)

    async def clear(self) -> None:
        """Clear local cache AND delete the session from Redis."""
        sid = self._require_session_id()
        self._messages.clear()
        self._write_count = 0
        try:
            await self.delete_session(sid)
        except Exception as exc:
            logger.warning(
                "RedisMemory: could not clear Redis session %s: %s", sid, exc
            )

    async def get_token_count(self) -> int:
        """Approximate token count (100 tokens per message heuristic)."""
        return len(self._messages) * 100

    async def restore(self, limit: Optional[int] = None) -> int:
        """Reload history from Redis into the local cache.

        Args:
            limit: If given, only the most recent *limit* messages are loaded
                   (``LRANGE key -limit -1``).  Pass ``model_context_window + 5``
                   for a fast hot-path fetch that still covers the full LLM window.
                   When ``None`` the full history is loaded.

        Returns the number of messages restored.  Must be called once after
        ``connect()`` to resume a session from a previous agent run.
        """
        sid = self._require_session_id()
        history = await self.fetch(sid, limit=limit)
        self._messages = list(history)
        logger.info(
            "RedisMemory: restored %d messages for session %s (limit=%s)",
            len(self._messages),
            sid,
            limit,
        )
        return len(self._messages)

    async def set_metadata(
        self, session_id_or_meta: Any = None, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Store session metadata as a Redis hash.

        Supports two calling conventions:
          - Per-session (no session_id at construction):
              ``await mem.set_metadata(session_id, metadata_dict)``
          - Per-session bound (session_id at construction):
              ``await mem.set_metadata(metadata_dict)``
        """
        if metadata is None:
            # Bound calling convention: set_metadata(meta_dict)
            sid = self._require_session_id()
            meta = session_id_or_meta
        else:
            # Multi-session calling convention: set_metadata(session_id, meta)
            sid = session_id_or_meta
            meta = metadata
        _validate_session_id(sid)
        client = self._require_client()
        key = self._meta_key(sid)
        flat: Dict[str, str] = {}
        for k, v in meta.items():
            flat[k] = json.dumps(v, default=str) if not isinstance(v, str) else v
        pipe = client.pipeline(transaction=True)
        if flat:
            pipe.hset(key, mapping=flat)
        if self._default_ttl > 0:
            pipe.expire(key, self._default_ttl)
        await pipe.execute()

    async def get_metadata(
        self, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Retrieve session metadata.

        ``session_id`` is optional when the instance has one baked in.
        """
        sid = session_id or self._require_session_id()
        _validate_session_id(sid)
        client = self._require_client()
        raw = await client.hgetall(self._meta_key(sid))
        result: Dict[str, Any] = {}
        for k, v in raw.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result

    # ── Low-level multi-session API (used by SessionManager) ─────────────────

    async def store(self, session_id: str, message: BaseClientMessage) -> None:
        """Write a single message directly to Redis for *session_id*."""
        _validate_session_id(session_id)
        client = self._require_client()
        key = self._msg_key(session_id)
        serialized = json.dumps(serialize_message(message), default=str)
        pipe = client.pipeline(transaction=True)
        pipe.rpush(key, serialized)
        if self._max_messages > 0:
            pipe.ltrim(key, -self._max_messages, -1)
        if self._default_ttl > 0:
            pipe.expire(key, self._default_ttl)
        await pipe.execute()

    async def store_many(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> None:
        """Write multiple messages in a single pipeline."""
        _validate_session_id(session_id)
        if not messages:
            return
        client = self._require_client()
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

    async def fetch(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        """Read messages for *session_id* directly from Redis."""
        _validate_session_id(session_id)
        client = self._require_client()
        key = self._msg_key(session_id)
        if limit is not None and limit > 0:
            raw_items = await client.lrange(key, -limit, -1)
        else:
            raw_items = await client.lrange(key, 0, -1)
        return [deserialize_message(json.loads(raw)) for raw in raw_items]

    async def count(self, session_id: str) -> int:
        """Return the number of messages stored for *session_id*."""
        _validate_session_id(session_id)
        client = self._require_client()
        return await client.llen(self._msg_key(session_id))

    async def drop(self, session_id: str) -> None:
        """Delete all messages for *session_id* (messages key only)."""
        _validate_session_id(session_id)
        client = self._require_client()
        await client.delete(self._msg_key(session_id))

    async def exists(self, session_id: str) -> bool:
        """Return True if *session_id* has messages in Redis."""
        _validate_session_id(session_id)
        client = self._require_client()
        return await client.exists(self._msg_key(session_id)) > 0

    async def refresh_ttl(
        self, session_id: str, ttl: Optional[int] = None
    ) -> None:
        """Reset TTL on messages and meta keys for *session_id*."""
        _validate_session_id(session_id)
        client = self._require_client()
        effective_ttl = ttl if ttl is not None else self._default_ttl
        if effective_ttl <= 0:
            return
        pipe = client.pipeline(transaction=True)
        pipe.expire(self._msg_key(session_id), effective_ttl)
        pipe.expire(self._meta_key(session_id), effective_ttl)
        await pipe.execute()

    async def get_ttl(self, session_id: str) -> int:
        """Return remaining TTL in seconds (-1 = no expiry, -2 = missing)."""
        _validate_session_id(session_id)
        client = self._require_client()
        return await client.ttl(self._msg_key(session_id))

    async def delete_session(self, session_id: str) -> None:
        """Remove all Redis keys for *session_id* (messages + metadata)."""
        _validate_session_id(session_id)
        client = self._require_client()
        await client.delete(
            self._msg_key(session_id),
            self._meta_key(session_id),
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _persist_one(self, session_id: str, message: BaseClientMessage) -> None:
        """Write one message to Redis; optionally checkpoint to Postgres."""
        try:
            await self.store(session_id, message)
            self._write_count += 1
        except Exception as exc:
            logger.error(
                "RedisMemory: failed to persist message (session %s): %s",
                session_id,
                exc,
            )
            return

        if (
            self._auto_checkpoint_every > 0
            and self._session_manager is not None
            and self._write_count % self._auto_checkpoint_every == 0
        ):
            try:
                await self._session_manager.checkpoint(session_id)  # type: ignore[attr-defined]
                logger.info(
                    "RedisMemory: auto-checkpointed session %s (every %d msgs)",
                    session_id,
                    self._auto_checkpoint_every,
                )
            except Exception as exc:
                logger.warning(
                    "RedisMemory: checkpoint failed for session %s: %s",
                    session_id,
                    exc,
                )

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "RedisMemory":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()

    def __repr__(self) -> str:
        sid = self._session_id or "<no session>"
        return (
            f"RedisMemory(session_id={sid!r}, "
            f"messages={len(self._messages)}, connected={self._client is not None})"
        )

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id
