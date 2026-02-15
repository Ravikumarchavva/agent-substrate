"""PostgreSQL-backed long-term memory store.

Provides durable, queryable persistence for session messages and metadata.
Uses SQLAlchemy 2.0 async ORM, reusing the project's existing DB layer patterns.

Tables (created automatically):
  ``memory_sessions``  — one row per session (metadata, timestamps, status).
  ``memory_messages``  — one row per message within a session (JSONB payload).

Design:
  - Fully async with ``asyncpg`` driver.
  - Separate from server models — memory is its own bounded context.
  - JSONB for message payloads — keeps the schema flexible while still queryable.
  - Cascading deletes: dropping a session removes its messages.

Security:
  - All queries use parameterized ORM operations — no raw SQL string interpolation.
  - Session IDs validated before every operation.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    delete,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from agent_framework.messages.base_message import BaseClientMessage
from agent_framework.memory.message_serializer import (
    serialize_message,
    deserialize_message,
)

logger = logging.getLogger("agent_framework.memory.postgres")

_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must match {_SESSION_ID_PATTERN.pattern}"
        )


# ---------------------------------------------------------------------------
# ORM Models (memory-specific, separate Base from server models)
# ---------------------------------------------------------------------------

class MemoryBase(DeclarativeBase):
    """Separate declarative base for the memory subsystem."""
    pass


class MemorySession(MemoryBase):
    """Persistent session record."""
    __tablename__ = "memory_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JSONB, default=dict
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    messages: Mapped[List["MemoryMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MemoryMessage.sequence",
    )

    def __repr__(self) -> str:
        return (
            f"<MemorySession(id={self.id!r}, agent={self.agent_name!r}, "
            f"status={self.status!r}, msgs={self.message_count})>"
        )


class MemoryMessage(MemoryBase):
    """Single message stored for a session."""
    __tablename__ = "memory_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_session_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("memory_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    session: Mapped["MemorySession"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<MemoryMessage(id={self.id}, session={self.session_id!r}, "
            f"seq={self.sequence}, type={self.message_type!r})>"
        )


# ---------------------------------------------------------------------------
# PostgresMemory
# ---------------------------------------------------------------------------

class PostgresMemory:
    """Async PostgreSQL-backed long-term message store.

    Parameters:
        database_url: PostgreSQL connection string
            (e.g. ``postgresql+asyncpg://user:pass@localhost/agentdb``).
        echo: If ``True``, log all SQL statements.
    """

    def __init__(
        self,
        database_url: str,
        echo: bool = False,
    ):
        self._database_url = database_url
        self._echo = echo
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Create the engine, session factory, and ensure tables exist."""
        if self._engine is not None:
            return

        self._engine = create_async_engine(
            self._database_url,
            echo=self._echo,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Create memory tables (idempotent)
        async with self._engine.begin() as conn:
            await conn.run_sync(MemoryBase.metadata.create_all)

        logger.info("PostgresMemory connected and tables ensured")

    async def disconnect(self) -> None:
        """Dispose of the engine."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("PostgresMemory disconnected")

    def _get_session(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "PostgresMemory not connected. Call await connect() first."
            )
        return self._session_factory

    # -- Session CRUD ---------------------------------------------------------

    async def create_session(
        self,
        session_id: str,
        agent_name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemorySession:
        """Create a new session record."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            session_obj = MemorySession(
                id=session_id,
                agent_name=agent_name,
                user_id=user_id,
                status="active",
                metadata_=metadata or {},
            )
            db.add(session_obj)
            await db.commit()
            await db.refresh(session_obj)
            logger.debug("Created session %s", session_id)
            return session_obj

    async def get_session(self, session_id: str) -> Optional[MemorySession]:
        """Retrieve a session by ID."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            return await db.get(MemorySession, session_id)

    async def update_session_status(
        self, session_id: str, status: str
    ) -> None:
        """Update the session status (active / closed / archived)."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            session_obj = await db.get(MemorySession, session_id)
            if session_obj is None:
                raise ValueError(f"Session '{session_id}' not found")
            session_obj.status = status
            await db.commit()

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and all its messages (cascade)."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            session_obj = await db.get(MemorySession, session_id)
            if session_obj:
                await db.delete(session_obj)
                await db.commit()
                logger.debug("Deleted session %s", session_id)

    async def list_sessions(
        self,
        agent_name: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[MemorySession]:
        """List sessions with optional filters."""
        factory = self._get_session()
        async with factory() as db:
            stmt = select(MemorySession).order_by(
                MemorySession.updated_at.desc()
            )
            if agent_name is not None:
                stmt = stmt.where(MemorySession.agent_name == agent_name)
            if user_id is not None:
                stmt = stmt.where(MemorySession.user_id == user_id)
            if status is not None:
                stmt = stmt.where(MemorySession.status == status)
            stmt = stmt.limit(limit).offset(offset)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    # -- Message CRUD ---------------------------------------------------------

    async def save_messages(
        self, session_id: str, messages: List[BaseClientMessage]
    ) -> int:
        """Persist a batch of messages for a session.

        Messages are assigned sequential IDs starting after the current max.
        Returns the number of messages saved.
        """
        _validate_session_id(session_id)
        if not messages:
            return 0

        factory = self._get_session()
        async with factory() as db:
            # Lock the session row to prevent concurrent writes to the same
            # session (fixes TOCTOU race on sequence counter).
            # Note: We can't use FOR UPDATE on the aggregate MAX() query directly,
            # so we lock the parent session row instead.
            session_obj = await db.get(
                MemorySession, session_id, with_for_update=True
            )
            if session_obj is None:
                raise ValueError(f"Session '{session_id}' not found")

            # Get current max sequence (now safe because session is locked)
            stmt = select(func.coalesce(func.max(MemoryMessage.sequence), 0)).where(
                MemoryMessage.session_id == session_id
            )
            result = await db.execute(stmt)
            max_seq: int = result.scalar_one()

            # Bulk insert messages
            for i, msg in enumerate(messages, start=max_seq + 1):
                payload = serialize_message(msg)
                db.add(MemoryMessage(
                    session_id=session_id,
                    sequence=i,
                    message_type=payload.get("type", type(msg).__name__),
                    payload=payload,
                ))

            # Update session message count
            session_obj.message_count = max_seq + len(messages)

            await db.commit()
            logger.debug(
                "Saved %d messages for session %s", len(messages), session_id
            )
            return len(messages)

    async def load_messages(
        self,
        session_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[BaseClientMessage]:
        """Load messages for a session, ordered by sequence.

        Args:
            session_id: Session identifier.
            limit: Max messages to return (``None`` = all).
            offset: Skip this many messages from the start.
        """
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(MemoryMessage)
                .where(MemoryMessage.session_id == session_id)
                .order_by(MemoryMessage.sequence)
                .offset(offset)
            )
            if limit is not None:
                stmt = stmt.limit(limit)

            result = await db.execute(stmt)
            rows = result.scalars().all()

            messages: List[BaseClientMessage] = []
            for row in rows:
                messages.append(deserialize_message(row.payload))
            return messages

    async def get_message_count(self, session_id: str) -> int:
        """Return the total number of persisted messages for a session."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(func.count())
                .select_from(MemoryMessage)
                .where(MemoryMessage.session_id == session_id)
            )
            result = await db.execute(stmt)
            return result.scalar_one()

    async def clear_messages(self, session_id: str) -> None:
        """Delete all messages for a session without deleting the session."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            stmt = delete(MemoryMessage).where(
                MemoryMessage.session_id == session_id
            )
            await db.execute(stmt)

            session_obj = await db.get(MemorySession, session_id)
            if session_obj is not None:
                session_obj.message_count = 0
            await db.commit()

    # -- Context manager ------------------------------------------------------

    async def __aenter__(self) -> "PostgresMemory":
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()
