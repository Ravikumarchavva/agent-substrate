"""Async database connection layer using SQLAlchemy + asyncpg."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from substrate.serving.monolith.models import Base

# Columns added to existing tables after they first shipped —
# `Base.metadata.create_all` below is a no-op on a pre-existing table (it
# only creates missing tables, never alters existing ones), so a column
# added to a model here never reaches an already-provisioned dev/staging DB
# without this. Mirrors the same additive-migration pattern used for
# substrate_run_queue in infrastructure/runtime/scheduler.py.
_MIGRATE_COLUMNS: list[tuple[str, str, str]] = [
    ("threads", "tenant_id", "VARCHAR"),
    ("file_metadata", "extracted_text", "TEXT"),
    ("file_metadata", "extracted_at", "TIMESTAMPTZ"),
    ("file_metadata", "extraction_engine", "VARCHAR"),
    ("file_versions", "restored_from_seq", "INTEGER"),
]


async def init_db(
    database_url: str,
    *,
    echo: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initialize the database engine and create tables.

    Returns ``(engine, session_factory)`` for the caller to store on
    ``app.state.*``.  No module-level globals are used.
    """
    engine = create_async_engine(
        database_url,
        echo=echo,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, col, defn in _MIGRATE_COLUMNS:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {defn}")
            )

    return engine, session_factory


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async session from ``app.state``."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
