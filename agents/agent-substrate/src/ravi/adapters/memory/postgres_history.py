"""PostgresHistoryProvider — PostgreSQL-backed durable conversation history.

Durable, queryable persistence for session messages using SQLAlchemy 2.0 async ORM.

Tables (created automatically):
  ``memory_sessions``  — one row per session (timestamps, message count).
  ``memory_messages``  — one row per message within a session (JSONB payload).

The session row is auto-created on first ``save_messages`` so the provider
works standalone (no separate session bookkeeping required).

Security:
  - All queries use parameterized ORM operations — no raw SQL interpolation.
  - Session IDs are validated before every operation.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ravi.adapters.llm.encoders.storage import (
    deserialize_message,
    serialize_message,
)
from ravi.kernel import ChatMessage
from ravi.logger import setup_logging

logger = setup_logging()

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
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List["MemoryMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MemoryMessage.sequence",
    )

    def __repr__(self) -> str:
        return f"<MemorySession(id={self.id!r}, msgs={self.message_count})>"


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

    session: Mapped["MemorySession"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<MemoryMessage(id={self.id}, session={self.session_id!r}, "
            f"seq={self.sequence}, type={self.message_type!r})>"
        )


# ---------------------------------------------------------------------------
# PostgresHistoryProvider
# ---------------------------------------------------------------------------


class PostgresHistoryProvider:
    """Async PostgreSQL-backed history provider.

    Parameters:
        database_url: PostgreSQL connection string
            (e.g. ``postgresql+asyncpg://user:pass@localhost/agentdb``).
        echo: If ``True``, log all SQL statements.
    """

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self._database_url = database_url
        self._echo = echo
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
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
        async with self._engine.begin() as conn:
            await conn.run_sync(MemoryBase.metadata.create_all)
        logger.info("PostgresHistoryProvider connected and tables ensured")

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("PostgresHistoryProvider disconnected")

    def _get_session(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "PostgresHistoryProvider not connected. Call await connect() first."
            )
        return self._session_factory

    # -- HistoryProvider protocol (kernel contract) ---------------------------

    def _session_key(self, agent_id: "AgentId", session_id: str) -> str:  # noqa: F821
        """Derive the internal storage key for a (agent_id, session_id) pair."""
        return f"{agent_id.type}:{agent_id.key}:{session_id}"

    async def append(
        self, agent_id: "AgentId", message: "Message", *, session_id: str  # noqa: F821
    ) -> None:
        storage_key = self._session_key(agent_id, session_id)
        payload = message.payload
        if hasattr(payload, "model_dump"):
            msgs: list[ChatMessage] = [payload]  # type: ignore[list-item]
        else:
            return
        await self.save_messages(storage_key, msgs)

    async def get_messages(
        self,
        agent_id: "AgentId",  # noqa: F821
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> "list[Message]":  # noqa: F821
        from ravi.kernel.message import Message as _Message
        storage_key = self._session_key(agent_id, session_id)
        chat_msgs = await self.load_messages(storage_key, limit=limit)
        if offset:
            chat_msgs = chat_msgs[offset:]
        results: list[_Message] = []
        for cm in chat_msgs:
            results.append(
                _Message(target=agent_id, payload=cm, sender=agent_id)
            )
        return results

    async def clear(
        self, agent_id: "AgentId", *, session_id: str  # noqa: F821
    ) -> None:
        storage_key = self._session_key(agent_id, session_id)
        await self.clear_session(storage_key)

    # -- Session-based API (legacy / internal) --------------------------------

    async def save_messages(self, session_id: str, messages: List[ChatMessage]) -> int:
        """Append messages to a session, auto-creating the session row.

        Messages are assigned sequential IDs after the current max.  The
        session row is locked to prevent concurrent writes racing on the
        sequence counter.  Returns the number of messages saved.
        """
        _validate_session_id(session_id)
        if not messages:
            return 0

        factory = self._get_session()
        async with factory() as db:
            session_obj = await db.get(MemorySession, session_id, with_for_update=True)
            if session_obj is None:
                session_obj = MemorySession(id=session_id, message_count=0)
                db.add(session_obj)
                await db.flush()

            stmt = select(func.coalesce(func.max(MemoryMessage.sequence), 0)).where(
                MemoryMessage.session_id == session_id
            )
            result = await db.execute(stmt)
            max_seq: int = result.scalar_one()

            for i, msg in enumerate(messages, start=max_seq + 1):
                payload = serialize_message(msg)
                db.add(
                    MemoryMessage(
                        session_id=session_id,
                        sequence=i,
                        message_type=payload.get("type", type(msg).__name__),
                        payload=payload,
                    )
                )

            session_obj.message_count = max_seq + len(messages)
            await db.commit()
            logger.debug("Saved %d messages for session %s", len(messages), session_id)
            return len(messages)

    async def load_messages(
        self, session_id: str, *, limit: Optional[int] = None
    ) -> List[ChatMessage]:
        """Load a session's messages ordered by sequence (last *limit* if given)."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            if limit is not None and limit > 0:
                # Take the last `limit` by sequence, then restore ascending order.
                stmt = (
                    select(MemoryMessage)
                    .where(MemoryMessage.session_id == session_id)
                    .order_by(MemoryMessage.sequence.desc())
                    .limit(limit)
                )
                result = await db.execute(stmt)
                rows = list(reversed(result.scalars().all()))
            else:
                stmt = (
                    select(MemoryMessage)
                    .where(MemoryMessage.session_id == session_id)
                    .order_by(MemoryMessage.sequence)
                )
                result = await db.execute(stmt)
                rows = list(result.scalars().all())

            return [deserialize_message(row.payload) for row in rows]

    async def count_messages(self, session_id: str) -> int:
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

    async def clear_session(self, session_id: str) -> None:
        """Delete all messages for a session (keeps the session row)."""
        _validate_session_id(session_id)
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(MemoryMessage).where(MemoryMessage.session_id == session_id)
            )
            session_obj = await db.get(MemorySession, session_id)
            if session_obj is not None:
                session_obj.message_count = 0
            await db.commit()
