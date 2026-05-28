"""PostgreSQL-backed MetadataStore implementation (cold-tier).

Uses SQLAlchemy 2.0 async ORM + asyncpg. Records default to the COLD tier
since Postgres is the archive / durable backend in the two-tier layout.
Callers may request HOT tier explicitly (e.g. during pipeline warm-up),
and :meth:`compact` sweeps stale HOT records back to COLD.

Table: ``metadata_records``
  composite_pk  — ``{tenant_id}:{key}`` (primary key)
  key           — String, indexed
  tenant_id     — String, indexed
  value_json    — JSONB
  tier          — String  ("hot" | "cold")
  created_at    — DateTime(tz=True)
  updated_at    — DateTime(tz=True)
  accessed_at   — DateTime(tz=True)
  etag          — String
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import DateTime, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ravi.kernel.metadata import (
    KeyNotFoundError,
    MetadataRecord,
    Tier,
    compute_etag,
)

__all__ = ["PostgresMetadataStore"]

UTC = timezone.utc
_HOT_IDLE_MINUTES = 5


def _now() -> datetime:
    return datetime.now(UTC)


def _composite_pk(tenant_id: str, key: str) -> str:
    return f"{tenant_id}:{key}"


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class MetadataBase(DeclarativeBase):
    """Separate declarative base for the metadata subsystem."""


class MetadataRow(MetadataBase):
    """ORM row for a single metadata record."""

    __tablename__ = "metadata_records"

    composite_pk: Mapped[str] = mapped_column(String(512), primary_key=True)
    key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="cold")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    etag: Mapped[str] = mapped_column(String(64), nullable=False, default="")


# ---------------------------------------------------------------------------
# Store implementation
# ---------------------------------------------------------------------------


def _row_to_record(row: MetadataRow) -> MetadataRecord:
    tier = Tier.HOT if row.tier == Tier.HOT.value else Tier.COLD
    return MetadataRecord(
        key=row.key,
        value=dict(row.value_json),
        tier=tier,
        tenant_id=row.tenant_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        accessed_at=row.accessed_at,
        etag=row.etag,
    )


class PostgresMetadataStore:
    """SQLAlchemy 2.0 async PostgreSQL :class:`MetadataStore`.

    Parameters
    ----------
    database_url:
        PostgreSQL async connection string, e.g.
        ``postgresql+asyncpg://user:pass@localhost/agentdb``.
    pool_size:
        SQLAlchemy connection pool size.
    """

    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 5,
    ) -> None:
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
            max_overflow=10,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with self._engine.begin() as conn:
            await conn.run_sync(MetadataBase.metadata.create_all)

    def _session(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError(
                "PostgresMetadataStore not initialised — call await init_db() first."
            )
        return self._session_factory()

    # ------------------------------------------------------------------
    # MetadataStore Protocol
    # ------------------------------------------------------------------

    async def put(
        self,
        key: str,
        value: dict[str, Any],
        *,
        tier: Tier = Tier.HOT,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Upsert a record; preserve ``created_at`` on update."""
        now = _now()
        etag = compute_etag(value)
        cpk = _composite_pk(tenant_id, key)

        async with self._session() as db:
            row = await db.get(MetadataRow, cpk)
            if row is None:
                row = MetadataRow(
                    composite_pk=cpk,
                    key=key,
                    tenant_id=tenant_id,
                    value_json=value,
                    tier=tier.value,
                    created_at=now,
                    updated_at=now,
                    accessed_at=now,
                    etag=etag,
                )
                db.add(row)
            else:
                # In-place update — keep created_at, keep current tier
                row.value_json = value
                row.updated_at = now
                row.accessed_at = now
                row.etag = etag
            await db.commit()
            await db.refresh(row)
            return _row_to_record(row)

    async def get(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Return the record; raise :class:`KeyNotFoundError` when absent."""
        record = await self.get_or_none(key, tenant_id=tenant_id)
        if record is None:
            raise KeyNotFoundError(key)
        return record

    async def get_or_none(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord | None:
        """Return the record or ``None`` when absent."""
        cpk = _composite_pk(tenant_id, key)
        now = _now()
        async with self._session() as db:
            row = await db.get(MetadataRow, cpk)
            if row is None:
                return None
            row.accessed_at = now
            await db.commit()
            await db.refresh(row)
            return _row_to_record(row)

    async def delete(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> bool:
        """Remove ``key``; return ``True`` when something was deleted."""
        cpk = _composite_pk(tenant_id, key)
        async with self._session() as db:
            row = await db.get(MetadataRow, cpk)
            if row is None:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def scan_prefix(
        self,
        prefix: str,
        *,
        tenant_id: str = "default",
        limit: int = 100,
    ) -> list[MetadataRecord]:
        """Return records whose key starts with ``prefix``, lexicographically sorted."""
        if limit <= 0:
            return []
        now = _now()
        like_pattern = f"{prefix}%"
        stmt = (
            select(MetadataRow)
            .where(MetadataRow.key.like(like_pattern))
            .where(MetadataRow.tenant_id == tenant_id)
            .order_by(MetadataRow.key)
            .limit(limit)
        )
        async with self._session() as db:
            result = await db.execute(stmt)
            rows = list(result.scalars().all())
            # Bump accessed_at for all matched rows
            for row in rows:
                row.accessed_at = now
            if rows:
                await db.commit()
            return [_row_to_record(row) for row in rows]

    async def promote(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Move ``key`` to :attr:`Tier.HOT`."""
        cpk = _composite_pk(tenant_id, key)
        now = _now()
        async with self._session() as db:
            row = await db.get(MetadataRow, cpk)
            if row is None:
                raise KeyNotFoundError(key)
            row.tier = Tier.HOT.value
            row.updated_at = now
            row.accessed_at = now
            await db.commit()
            await db.refresh(row)
            return _row_to_record(row)

    async def demote(
        self,
        key: str,
        *,
        tenant_id: str = "default",
    ) -> MetadataRecord:
        """Move ``key`` to :attr:`Tier.COLD`."""
        cpk = _composite_pk(tenant_id, key)
        now = _now()
        async with self._session() as db:
            row = await db.get(MetadataRow, cpk)
            if row is None:
                raise KeyNotFoundError(key)
            row.tier = Tier.COLD.value
            row.updated_at = now
            row.accessed_at = now
            await db.commit()
            await db.refresh(row)
            return _row_to_record(row)

    async def compact(
        self,
        *,
        tenant_id: str = "default",
    ) -> int:
        """Demote HOT records idle for more than ``_HOT_IDLE_MINUTES`` minutes."""
        cutoff = _now() - timedelta(minutes=_HOT_IDLE_MINUTES)
        now = _now()
        stmt = (
            select(MetadataRow)
            .where(MetadataRow.tenant_id == tenant_id)
            .where(MetadataRow.tier == Tier.HOT.value)
            .where(MetadataRow.accessed_at < cutoff)
        )
        async with self._session() as db:
            result = await db.execute(stmt)
            rows = list(result.scalars().all())
            for row in rows:
                row.tier = Tier.COLD.value
                row.updated_at = now
            if rows:
                await db.commit()
            return len(rows)
