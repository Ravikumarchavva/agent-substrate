"""PostgresHistoryProvider — PostgreSQL-backed durable conversation history.

Durable, queryable persistence for session messages using SQLAlchemy 2.0 async ORM.

Tables (created automatically):
  ``memory_sessions``  — one row per session (timestamps, message count).
  ``memory_messages``  — one row per message within a session (JSONB payload).

The session row is auto-created on first ``save_messages`` so the provider
works standalone (no separate session bookkeeping required).

Internal storage key:
  The public ``HistoryProvider`` methods (``append``, ``get_messages``, ``clear``)
  compose an internal key of the form ``{agent_type}:{agent_key}:{session_id}``
  using ``_session_key()``.  Validation runs on the raw ``session_id`` at the
  public boundary — the composed key is internal and intentionally contains ``:``.

Security:
  - All queries use parameterized ORM operations — no raw SQL interpolation.
  - Raw session IDs are validated at the public protocol boundary.
"""

from __future__ import annotations

import re
import uuid
import hashlib
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

from ravi.kernel import ChatMessage, Message, AgentId
from ravi.logger import setup_logging

logger = setup_logging()


# ── Message serialisation (provider-agnostic JSON round-trip) ─────────────────


def _bytes_to_b64(val: Any) -> Any:
    import base64

    if isinstance(val, dict):
        return {k: _bytes_to_b64(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_bytes_to_b64(x) for x in val]
    if isinstance(val, bytes):
        return {"__bytes_b64__": base64.b64encode(val).decode("utf-8")}
    return val


def _b64_to_bytes(val: Any) -> Any:
    import base64

    if isinstance(val, dict):
        if "__bytes_b64__" in val:
            return base64.b64decode(val["__bytes_b64__"])
        return {k: _b64_to_bytes(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_b64_to_bytes(x) for x in val]
    return val


def serialize_message(message: ChatMessage) -> Dict[str, Any]:
    """Serialize a ChatMessage to a dict suitable for JSONB storage."""
    return _bytes_to_b64(message.model_dump())


def deserialize_message(data: Dict[str, Any]) -> ChatMessage:
    """Deserialize a JSONB dict back to a ChatMessage."""
    return ChatMessage.model_validate(_b64_to_bytes(data))


# ─────────────────────────────────────────────────────────────────────────────

_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_MAX_STORAGE_SESSION_KEY_LENGTH = 128


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session_id: must match {_SESSION_ID_PATTERN.pattern}"
        )


# ---------------------------------------------------------------------------
# ORM Models — separate Base from server models to avoid metadata conflicts
# ---------------------------------------------------------------------------


class HistoryBase(DeclarativeBase):
    """Separate declarative base for the history subsystem."""

    pass


class HistorySession(HistoryBase):
    """Persistent session record.  Table name kept as ``memory_sessions`` for
    backwards compatibility with existing deployments."""

    __tablename__ = "memory_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List["HistoryMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="HistoryMessage.sequence",
    )

    def __repr__(self) -> str:
        return f"<HistorySession(id={self.id!r}, msgs={self.message_count})>"


class HistoryMessage(HistoryBase):
    """Single message stored for a session.  Table name kept as
    ``memory_messages`` for backwards compatibility."""

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
    run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="", index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    session: Mapped[HistorySession] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"<HistoryMessage(id={self.id}, session={self.session_id!r}, "
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
            await conn.run_sync(HistoryBase.metadata.create_all)
            from sqlalchemy import text as _text
            await conn.execute(
                _text("ALTER TABLE memory_messages ADD COLUMN IF NOT EXISTS run_id VARCHAR(64) NOT NULL DEFAULT ''")
            )
            await conn.execute(
                _text("CREATE INDEX IF NOT EXISTS ix_memory_messages_run_id ON memory_messages (run_id)")
            )
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

    def _session_key(self, agent_id: AgentId, session_id: str) -> str:
        """Derive the internal storage key for a (agent_id, session_id) pair."""
        key = f"{agent_id.type}:{agent_id.key}:{session_id}"
        if len(key) <= _MAX_STORAGE_SESSION_KEY_LENGTH:
            return key
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return f"h:{digest}"

    async def append(
        self,
        agent_id: AgentId,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        await self.save_messages(storage_key, [message], run_id=run_id)

    async def append_many(
        self,
        agent_id: AgentId,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        await self.save_messages(storage_key, messages, run_id=run_id)

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        return await self.load_messages(storage_key, limit=limit, offset=offset)

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        await self.clear_session(storage_key)

    async def clear_run(
        self, agent_id: AgentId, *, session_id: str, run_id: str
    ) -> None:
        _validate_session_id(session_id)
        storage_key = self._session_key(agent_id, session_id)
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(HistoryMessage).where(
                    HistoryMessage.session_id == storage_key,
                    HistoryMessage.run_id == run_id
                )
            )
            session_obj = await db.get(HistorySession, storage_key, with_for_update=True)
            if session_obj is not None:
                stmt = select(func.count()).select_from(HistoryMessage).where(
                    HistoryMessage.session_id == storage_key
                )
                result = await db.execute(stmt)
                session_obj.message_count = result.scalar_one()
            await db.commit()

    # -- Session-based API (legacy / internal) --------------------------------

    async def save_messages(
        self, session_id: str, messages: List[ChatMessage], run_id: str = ""
    ) -> int:
        """Append messages to a session, auto-creating the session row.

        Messages are assigned sequential IDs after the current max.  The
        session row is locked to prevent concurrent writes racing on the
        sequence counter.  Returns the number of messages saved.
        """
        if not messages:
            return 0

        factory = self._get_session()
        async with factory() as db:
            session_obj = await db.get(HistorySession, session_id, with_for_update=True)
            if session_obj is None:
                session_obj = HistorySession(id=session_id, message_count=0)
                db.add(session_obj)
                await db.flush()

            stmt = select(func.coalesce(func.max(HistoryMessage.sequence), 0)).where(
                HistoryMessage.session_id == session_id
            )
            result = await db.execute(stmt)
            max_seq: int = result.scalar_one()

            for i, msg in enumerate(messages, start=max_seq + 1):
                payload = serialize_message(msg)
                db.add(
                    HistoryMessage(
                        session_id=session_id,
                        sequence=i,
                        message_type=payload.get("type", type(msg).__name__),
                        payload=payload,
                        run_id=run_id,
                    )
                )

            session_obj.message_count = max_seq + len(messages)
            await db.commit()
            logger.debug("Saved %d messages for session %s (run_id=%s)", len(messages), session_id, run_id)
            return len(messages)

    async def load_messages(
        self, session_id: str, *, limit: Optional[int] = None, offset: Optional[int] = None
    ) -> List[ChatMessage]:
        """Load a session's messages ordered by sequence (last *limit* if given)."""
        factory = self._get_session()
        async with factory() as db:
            if limit is not None and limit > 0 and offset is None:
                # Take the last `limit` by sequence, then restore ascending order.
                stmt = (
                    select(HistoryMessage)
                    .where(HistoryMessage.session_id == session_id)
                    .order_by(HistoryMessage.sequence.desc())
                    .limit(limit)
                )
                result = await db.execute(stmt)
                rows = list(reversed(result.scalars().all()))
            else:
                stmt = (
                    select(HistoryMessage)
                    .where(HistoryMessage.session_id == session_id)
                    .order_by(HistoryMessage.sequence)
                )
                if offset is not None:
                    stmt = stmt.offset(offset)
                if limit is not None:
                    stmt = stmt.limit(limit)
                result = await db.execute(stmt)
                rows = list(result.scalars().all())

            return [deserialize_message(row.payload) for row in rows]

    async def count_messages(self, session_id: str) -> int:
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(func.count())
                .select_from(HistoryMessage)
                .where(HistoryMessage.session_id == session_id)
            )
            result = await db.execute(stmt)
            return result.scalar_one()

    async def clear_session(self, session_id: str) -> None:
        """Delete all messages for a session (keeps the session row)."""
        factory = self._get_session()
        async with factory() as db:
            await db.execute(
                delete(HistoryMessage).where(HistoryMessage.session_id == session_id)
            )
            session_obj = await db.get(HistorySession, session_id)
            if session_obj is not None:
                session_obj.message_count = 0
            await db.commit()
