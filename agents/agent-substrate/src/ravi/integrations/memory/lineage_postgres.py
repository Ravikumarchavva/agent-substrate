"""PostgreSQL-backed Memory Lineage Store — Section 9 (Memory + Graph Redesign).

Implements :class:`~ravi.kernel.memory._lineage.LineageStore` using
SQLAlchemy 2.0 async ORM with asyncpg.

Tables (created automatically via :meth:`PostgresLineageStore.init_db`):
  ``lineage_records``  — one row per (session_id, message_id) pair.

Design:
  - Fully async with ``asyncpg`` driver.
  - Separate ``LineageBase`` from other ORM bases — lineage is its own
    bounded context.
  - Upsert via ``INSERT … ON CONFLICT DO UPDATE`` to keep ``record()``
    idempotent.
  - Causal chain traversal detects cycles in O(n) via a ``seen`` set.

Security:
  - All queries use parameterised ORM operations — no raw SQL string
    interpolation.
  - Both ``session_id`` and ``message_id`` are validated against their
    respective regex patterns before every operation.
"""

from __future__ import annotations

import re
from typing import Sequence

from sqlalchemy import Float, Integer, String, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ravi.kernel.memory._lineage import (
    LineageNotFoundError,
    LineageRecord,
    LineageStore,
    ProvenanceTag,
    StorageTier,
)

__all__ = ["PostgresLineageStore"]

# ---------------------------------------------------------------------------
# ID validation
# ---------------------------------------------------------------------------

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_MSG_ID_RE = re.compile(r"^[a-zA-Z0-9_:/-]{1,256}$")


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(
            f"Invalid session_id {session_id!r}; must match {_SESSION_ID_RE.pattern}"
        )


def _validate_message_id(message_id: str) -> None:
    if not _MSG_ID_RE.match(message_id):
        raise ValueError(
            f"Invalid message_id {message_id!r}; must match {_MSG_ID_RE.pattern}"
        )


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class LineageBase(DeclarativeBase):
    """Separate declarative base for the lineage subsystem."""

    pass


class LineageRow(LineageBase):
    """Persisted lineage record."""

    __tablename__ = "lineage_records"
    __table_args__ = (
        UniqueConstraint("session_id", "message_id", name="uq_lineage_session_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_fqn: Mapped[str] = mapped_column(String, nullable=False)
    activation_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp_utc: Mapped[str] = mapped_column(String, nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trust_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier_str: Mapped[str] = mapped_column(String, nullable=False, default="warm")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LineageRow(session={self.session_id!r}, "
            f"message={self.message_id!r}, agent={self.agent_fqn!r})>"
        )


# ---------------------------------------------------------------------------
# Row → dataclass conversion
# ---------------------------------------------------------------------------


def _row_to_record(row: LineageRow) -> LineageRecord:
    return LineageRecord(
        session_id=row.session_id,
        message_id=row.message_id,
        provenance=ProvenanceTag(
            agent_fqn=row.agent_fqn,
            activation_id=row.activation_id,
            timestamp_utc=row.timestamp_utc,
            tool_call_id=row.tool_call_id,
            parent_message_id=row.parent_message_id,
            trust_score=row.trust_score,
        ),
        tier=StorageTier.WARM,
    )


# ---------------------------------------------------------------------------
# PostgresLineageStore
# ---------------------------------------------------------------------------


class PostgresLineageStore:
    """Async PostgreSQL-backed :class:`LineageStore` implementation.

    Parameters
    ----------
    database_url:
        PostgreSQL connection string
        (e.g. ``postgresql+asyncpg://user:pass@localhost/agentdb``).
    pool_size:
        SQLAlchemy engine connection pool size.  Defaults to 5.
    """

    def __init__(self, database_url: str, *, pool_size: int = 5) -> None:
        self._database_url = database_url
        self._pool_size = pool_size
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """Create the engine, session factory, and ensure tables exist."""
        if self._engine is not None:
            return

        self._engine = create_async_engine(
            self._database_url,
            pool_size=self._pool_size,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with self._engine.begin() as conn:
            await conn.run_sync(LineageBase.metadata.create_all)

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "PostgresLineageStore not initialised. Call await init_db() first."
            )
        return self._session_factory

    # ------------------------------------------------------------------
    # LineageStore protocol
    # ------------------------------------------------------------------

    @property
    def tier(self) -> StorageTier:
        """Declare the storage tier — always :attr:`StorageTier.WARM`."""
        return StorageTier.WARM

    async def record(
        self,
        session_id: str,
        message_id: str,
        provenance: ProvenanceTag,
    ) -> LineageRecord:
        """Persist a lineage record; idempotent via upsert.

        Raises :class:`ValueError` on invalid ``session_id`` or
        ``message_id``.
        """
        _validate_session_id(session_id)
        _validate_message_id(message_id)

        factory = self._get_session_factory()
        async with factory() as db:
            stmt = (
                pg_insert(LineageRow)
                .values(
                    session_id=session_id,
                    message_id=message_id,
                    agent_fqn=provenance.agent_fqn,
                    activation_id=provenance.activation_id,
                    timestamp_utc=provenance.timestamp_utc,
                    tool_call_id=provenance.tool_call_id,
                    parent_message_id=provenance.parent_message_id,
                    trust_score=provenance.trust_score,
                    tier_str="warm",
                )
                .on_conflict_do_update(
                    constraint="uq_lineage_session_message",
                    set_={
                        "agent_fqn": provenance.agent_fqn,
                        "activation_id": provenance.activation_id,
                        "timestamp_utc": provenance.timestamp_utc,
                        "tool_call_id": provenance.tool_call_id,
                        "parent_message_id": provenance.parent_message_id,
                        "trust_score": provenance.trust_score,
                        "tier_str": "warm",
                    },
                )
                .returning(LineageRow)
            )
            result = await db.execute(stmt)
            await db.commit()
            result.fetchone()  # consume result; upsert is self-contained

        # Reconstruct directly from the provenance we wrote (avoids an extra
        # SELECT and is safe because the upsert is atomic).
        return LineageRecord(
            session_id=session_id,
            message_id=message_id,
            provenance=provenance,
            tier=StorageTier.WARM,
        )

    async def get(self, session_id: str, message_id: str) -> LineageRecord:
        """Fetch the lineage record for ``message_id`` in ``session_id``.

        Raises :class:`LineageNotFoundError` when no record exists.
        """
        _validate_session_id(session_id)
        _validate_message_id(message_id)

        factory = self._get_session_factory()
        async with factory() as db:
            stmt = select(LineageRow).where(
                LineageRow.session_id == session_id,
                LineageRow.message_id == message_id,
            )
            result = await db.execute(stmt)
            row = result.scalar_one_or_none()

        if row is None:
            raise LineageNotFoundError(
                f"No lineage record for session={session_id!r} "
                f"message={message_id!r}"
            )
        return _row_to_record(row)

    async def list_session(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> Sequence[LineageRecord]:
        """Return all lineage records for a session, ordered by insertion (id ASC).

        ``limit`` caps the result count.  Raises :class:`ValueError` on
        invalid ``session_id``.
        """
        _validate_session_id(session_id)

        factory = self._get_session_factory()
        async with factory() as db:
            stmt = (
                select(LineageRow)
                .where(LineageRow.session_id == session_id)
                .order_by(LineageRow.id.asc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await db.execute(stmt)
            rows = result.scalars().all()

        return [_row_to_record(r) for r in rows]

    async def causal_chain(
        self,
        session_id: str,
        message_id: str,
    ) -> Sequence[LineageRecord]:
        """Follow ``parent_message_id`` links to return the full causal chain.

        Returns records oldest-first (root message first, ``message_id``
        last).  Raises :class:`LineageNotFoundError` if ``message_id`` has
        no record.  Cycles are detected via a ``seen`` set and stop
        traversal immediately.
        """
        _validate_session_id(session_id)
        _validate_message_id(message_id)

        factory = self._get_session_factory()

        # Verify the start record exists first.
        async with factory() as db:
            stmt = select(LineageRow).where(
                LineageRow.session_id == session_id,
                LineageRow.message_id == message_id,
            )
            result = await db.execute(stmt)
            start_row = result.scalar_one_or_none()

        if start_row is None:
            raise LineageNotFoundError(
                f"No lineage record for session={session_id!r} "
                f"message={message_id!r}"
            )

        # Walk parent links, one SELECT per hop.
        chain: list[LineageRecord] = []
        seen: set[str] = set()
        current_id: str | None = message_id

        while current_id is not None and current_id not in seen:
            async with factory() as db:
                stmt = select(LineageRow).where(
                    LineageRow.session_id == session_id,
                    LineageRow.message_id == current_id,
                )
                result = await db.execute(stmt)
                row = result.scalar_one_or_none()

            if row is None:
                break

            seen.add(current_id)
            chain.append(_row_to_record(row))
            current_id = row.parent_message_id

        chain.reverse()  # oldest (root) first
        return chain

    async def drop_session(self, session_id: str) -> None:
        """Delete all lineage records for ``session_id``.

        Raises :class:`ValueError` on invalid ``session_id``.
        """
        _validate_session_id(session_id)

        factory = self._get_session_factory()
        async with factory() as db:
            stmt = delete(LineageRow).where(LineageRow.session_id == session_id)
            await db.execute(stmt)
            await db.commit()

    # ------------------------------------------------------------------
    # Protocol runtime-check support
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PostgresLineageStore(url={self._database_url!r}, "
            f"pool_size={self._pool_size})"
        )


# Verify structural conformance at import time (runtime_checkable Protocol).
assert isinstance(PostgresLineageStore("postgresql+asyncpg://x/y"), LineageStore)
