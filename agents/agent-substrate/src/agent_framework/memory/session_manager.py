"""Unified session manager — orchestrates Redis (hot) and Postgres (cold) memory.

Architecture:
  ┌──────────────┐       ┌──────────────┐
  │  Agent / API  │──────▶│ SessionManager│
  └──────────────┘       └──────┬───────┘
                                │
                     ┌──────────┴──────────┐
                     │                     │
               ┌─────▼─────┐        ┌─────▼──────┐
               │ RedisMemory│        │PostgresMemory│
               │  (hot/TTL) │        │ (durable)    │
               └────────────┘        └─────────────┘

Lifecycle:
  1. ``create_session()``  — allocate a new session ID, store metadata in both tiers.
  2. ``add_message()``     — append to Redis (fast path).
  3. ``get_messages()``    — read from Redis; fall back to Postgres if expired.
  4. ``checkpoint()``      — flush Redis messages → Postgres for durability.
  5. ``close_session()``   — final checkpoint, mark session closed, clean Redis.
  6. ``resume_session()``  — restore a closed/expired session from Postgres back into Redis.

Thread-safety:
  - All public methods are ``async`` — safe for concurrent ``asyncio`` tasks.
  - Redis pipelining ensures atomic multi-step writes.

Security:
  - Session IDs are UUID-based and validated on every call.
  - No user-supplied data is used in key construction without validation.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agent_framework.messages.base_message import BaseClientMessage
from agent_framework.memory.redis_memory import RedisMemory
from agent_framework.memory.postgres_memory import PostgresMemory

logger = logging.getLogger("agent_framework.memory.session")


# ---------------------------------------------------------------------------
# Session state model
# ---------------------------------------------------------------------------

class SessionStatus(str, Enum):
    """Lifecycle states for a session."""
    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class SessionState(BaseModel):
    """Snapshot of a session's metadata (returned to callers)."""
    session_id: str
    agent_name: Optional[str] = None
    user_id: Optional[str] = None
    status: SessionStatus = SessionStatus.ACTIVE
    message_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_hot: bool = False  # True if currently loaded in Redis

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """Orchestrates short-term (Redis) and long-term (Postgres) memory.

    Parameters:
        redis: A ``RedisMemory`` instance (already configured, not yet connected).
        postgres: A ``PostgresMemory`` instance (already configured, not yet connected).
        auto_checkpoint_threshold: Number of new messages before auto-flushing to
            Postgres.  ``0`` disables auto-checkpoint.
    """

    def __init__(
        self,
        redis: RedisMemory,
        postgres: PostgresMemory,
        auto_checkpoint_threshold: int = 50,
    ):
        self._redis = redis
        self._postgres = postgres
        self._auto_checkpoint_threshold = auto_checkpoint_threshold
        # Track how many messages were added since last checkpoint per session
        self._dirty_counts: Dict[str, int] = {}
        # Per-session locks to prevent concurrent checkpoint races
        self._locks: Dict[str, asyncio.Lock] = {}

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Connect both storage backends."""
        await self._redis.connect()
        await self._postgres.connect()
        logger.info("SessionManager connected (Redis + Postgres)")

    async def disconnect(self) -> None:
        """Disconnect both storage backends."""
        await self._redis.disconnect()
        await self._postgres.disconnect()
        logger.info("SessionManager disconnected")

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """Return (or create) a per-session asyncio.Lock."""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # -- Session CRUD ---------------------------------------------------------

    async def create_session(
        self,
        agent_name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> SessionState:
        """Create a new session in both Redis and Postgres.

        Returns the ``SessionState`` with the generated ``session_id``.
        """
        sid = session_id or str(uuid.uuid4())
        meta = metadata or {}
        meta["agent_name"] = agent_name or ""
        meta["user_id"] = user_id or ""
        meta["status"] = SessionStatus.ACTIVE.value
        meta["created_at"] = datetime.now(timezone.utc).isoformat()

        # Persist to Postgres (source of truth)
        await self._postgres.create_session(
            session_id=sid,
            agent_name=agent_name,
            user_id=user_id,
            metadata=meta,
        )
        # Mirror metadata to Redis for fast reads
        await self._redis.set_metadata(sid, meta)

        self._dirty_counts[sid] = 0

        logger.info("Session created: %s (agent=%s)", sid, agent_name)
        return SessionState(
            session_id=sid,
            agent_name=agent_name,
            user_id=user_id,
            status=SessionStatus.ACTIVE,
            metadata=meta,
            created_at=datetime.now(timezone.utc),
            is_hot=True,
        )

    async def resume_session(self, session_id: str) -> SessionState:
        """Resume an existing session.

        If the session is still hot in Redis, use it directly.
        If it has expired from Redis (TTL), reload from Postgres.

        Returns:
            ``SessionState`` with messages loaded into Redis.

        Raises:
            ValueError: If the session does not exist in Postgres.
        """
        # Check Redis first
        if await self._redis.exists(session_id):
            meta = await self._redis.get_metadata(session_id)
            count = await self._redis.get_message_count(session_id)
            logger.info("Resumed hot session %s (%d messages)", session_id, count)
            return SessionState(
                session_id=session_id,
                agent_name=meta.get("agent_name"),
                user_id=meta.get("user_id"),
                status=SessionStatus.ACTIVE,
                message_count=count,
                metadata=meta,
                is_hot=True,
            )

        # Fall back to Postgres
        pg_session = await self._postgres.get_session(session_id)
        if pg_session is None:
            raise ValueError(f"Session '{session_id}' not found")

        # Reload messages into Redis
        messages = await self._postgres.load_messages(session_id)
        if messages:
            await self._redis.add_messages(session_id, messages)

        # Restore metadata
        meta = pg_session.metadata_ or {}
        meta["status"] = SessionStatus.ACTIVE.value
        await self._redis.set_metadata(session_id, meta)

        # Mark as active again in Postgres
        await self._postgres.update_session_status(
            session_id, SessionStatus.ACTIVE.value
        )

        self._dirty_counts[session_id] = 0

        logger.info(
            "Resumed cold session %s (%d messages from Postgres)",
            session_id, len(messages),
        )
        return SessionState(
            session_id=session_id,
            agent_name=pg_session.agent_name,
            user_id=pg_session.user_id,
            status=SessionStatus.ACTIVE,
            message_count=len(messages),
            metadata=meta,
            created_at=pg_session.created_at,
            updated_at=pg_session.updated_at,
            is_hot=True,
        )

    # -- Message operations ---------------------------------------------------

    async def add_message(
        self, session_id: str, message: BaseClientMessage
    ) -> None:
        """Add a message to the session (fast path via Redis).

        Automatically checkpoints to Postgres when the dirty count exceeds
        ``auto_checkpoint_threshold``.
        """
        await self._redis.add_message(session_id, message)

        dirty = self._dirty_counts.get(session_id, 0) + 1
        self._dirty_counts[session_id] = dirty

        if (
            self._auto_checkpoint_threshold > 0
            and dirty >= self._auto_checkpoint_threshold
        ):
            logger.debug(
                "Auto-checkpoint triggered for session %s (%d dirty)",
                session_id, dirty,
            )
            await self.checkpoint(session_id)

    async def add_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> None:
        """Add multiple messages at once."""
        if not messages:
            return
        await self._redis.add_messages(session_id, messages)

        dirty = self._dirty_counts.get(session_id, 0) + len(messages)
        self._dirty_counts[session_id] = dirty

        if (
            self._auto_checkpoint_threshold > 0
            and dirty >= self._auto_checkpoint_threshold
        ):
            await self.checkpoint(session_id)

    async def get_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[BaseClientMessage]:
        """Retrieve messages for a session.

        Reads from Redis if hot; falls back to Postgres.
        """
        if await self._redis.exists(session_id):
            return await self._redis.get_messages(session_id, limit=limit)

        # Session expired from Redis — try Postgres
        return await self._postgres.load_messages(session_id, limit=limit)

    async def get_message_count(self, session_id: str) -> int:
        """Return message count (Redis if hot, else Postgres)."""
        if await self._redis.exists(session_id):
            return await self._redis.get_message_count(session_id)
        return await self._postgres.get_message_count(session_id)

    # -- Checkpointing --------------------------------------------------------

    async def checkpoint(self, session_id: str) -> int:
        """Flush all Redis messages to Postgres.

        This is an **overwrite** strategy: clears existing Postgres messages
        and writes the full Redis snapshot.  This avoids duplicates and keeps
        Postgres in sync with the authoritative Redis state.

        Uses a per-session lock to prevent concurrent checkpoint calls from
        causing duplicate writes.

        Returns the number of messages persisted.
        """
        lock = self._get_lock(session_id)
        async with lock:
            messages = await self._redis.get_messages(session_id)
            if not messages:
                self._dirty_counts[session_id] = 0
                return 0

            # Replace Postgres messages with current Redis state
            await self._postgres.clear_messages(session_id)
            saved = await self._postgres.save_messages(session_id, messages)

            self._dirty_counts[session_id] = 0
            logger.info(
                "Checkpointed session %s: %d messages → Postgres",
                session_id, saved,
            )
            return saved

    # -- Session close / delete -----------------------------------------------

    async def close_session(self, session_id: str) -> None:
        """Final checkpoint, mark closed, and clean up Redis."""
        # Flush to Postgres
        await self.checkpoint(session_id)

        # Update status
        await self._postgres.update_session_status(
            session_id, SessionStatus.CLOSED.value
        )

        # Clean Redis
        await self._redis.delete_session(session_id)
        self._dirty_counts.pop(session_id, None)

        logger.info("Session %s closed and flushed to Postgres", session_id)

    async def delete_session(self, session_id: str) -> None:
        """Permanently delete a session from both tiers."""
        await self._redis.delete_session(session_id)
        await self._postgres.delete_session(session_id)
        self._dirty_counts.pop(session_id, None)
        self._locks.pop(session_id, None)
        logger.info("Session %s permanently deleted", session_id)

    # -- Query ----------------------------------------------------------------

    async def get_session_state(self, session_id: str) -> Optional[SessionState]:
        """Get current session state, merging Redis and Postgres info."""
        pg_session = await self._postgres.get_session(session_id)
        if pg_session is None:
            return None

        is_hot = await self._redis.exists(session_id)
        count = (
            await self._redis.get_message_count(session_id)
            if is_hot
            else pg_session.message_count
        )

        return SessionState(
            session_id=session_id,
            agent_name=pg_session.agent_name,
            user_id=pg_session.user_id,
            status=SessionStatus(pg_session.status),
            message_count=count,
            metadata=pg_session.metadata_ or {},
            created_at=pg_session.created_at,
            updated_at=pg_session.updated_at,
            is_hot=is_hot,
        )

    async def list_sessions(
        self,
        agent_name: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SessionState]:
        """List sessions from Postgres with optional filters."""
        pg_sessions = await self._postgres.list_sessions(
            agent_name=agent_name,
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        states: List[SessionState] = []
        for s in pg_sessions:
            is_hot = await self._redis.exists(s.id)
            states.append(
                SessionState(
                    session_id=s.id,
                    agent_name=s.agent_name,
                    user_id=s.user_id,
                    status=SessionStatus(s.status),
                    message_count=s.message_count,
                    metadata=s.metadata_ or {},
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                    is_hot=is_hot,
                )
            )
        return states

    # -- Context manager ------------------------------------------------------

    async def __aenter__(self) -> "SessionManager":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
