"""Async database connection layer using SQLAlchemy + asyncpg."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from substrate_cloud.monolith.models import Base
from substrate_cloud.monolith.rls import enable_row_level_security, ensure_app_role

# Columns added to existing tables after they first shipped —
# `Base.metadata.create_all` below is a no-op on a pre-existing table (it
# only creates missing tables, never alters existing ones), so a column
# added to a model here never reaches an already-provisioned dev/staging DB
# without this. Mirrors the same additive-migration pattern used for
# run_queue in integrations/runtime/scheduler.py.
_MIGRATE_COLUMNS: list[tuple[str, str, str]] = [
    ("threads", "tenant_id", "VARCHAR"),
    ("threads", "deleted_at", "TIMESTAMPTZ"),
    ("threads", "locked_at", "TIMESTAMPTZ"),
    ("threads", "locked_reason", "TEXT"),
    ("threads", "pinned_at", "TIMESTAMPTZ"),
    ("threads", "archived_at", "TIMESTAMPTZ"),
    ("threads", "agent_id", "UUID REFERENCES agents(id) ON DELETE SET NULL"),
    ("scheduled_tasks", "email_results", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("scheduled_tasks", "notify_email", "VARCHAR"),
    ("scheduled_tasks", "last_claimed_at", "TIMESTAMPTZ"),
    ("scheduled_tasks", "ask_before_acting", "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("user_preferences", "display_name", "VARCHAR"),
    ("groups", "token_cap", "INTEGER NOT NULL DEFAULT 1000000"),
    ("groups", "budget_usd", "DOUBLE PRECISION"),
    ("groups", "avatar_key", "VARCHAR"),
    ("groups", "pinned_at", "TIMESTAMPTZ"),
    ("agents", "avatar_key", "VARCHAR"),
    ("agents", "model", "VARCHAR"),
    ("agents", "pinned_at", "TIMESTAMPTZ"),
    ("scheduled_task_runs", "tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("scheduled_task_runs", "cost_usd", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    ("file_metadata", "extracted_text", "TEXT"),
    ("file_metadata", "extracted_at", "TIMESTAMPTZ"),
    ("file_metadata", "extraction_engine", "VARCHAR"),
    ("file_metadata", "rag_ingested_at", "TIMESTAMPTZ"),
    ("file_metadata", "page_count", "INTEGER"),
    ("file_metadata", "staged_at", "TIMESTAMPTZ"),
    ("file_metadata", "staging_error", "TEXT"),
    ("file_metadata", "promoted_at", "TIMESTAMPTZ"),
    ("file_versions", "restored_from_seq", "INTEGER"),
    ("file_versions", "tenant_id", "VARCHAR"),
]


async def init_db(
    database_url: str,
    *,
    echo: bool = False,
    rls_app_role_password: str | None = None,
    app_database_url: str | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Run schema setup on the admin connection, then return an engine/
    session_factory bound to the app's actual runtime connection.

    ``database_url`` is the admin/bootstrap connection — table creation,
    additive-column migrations, and RLS policy/role setup (see ``rls.py``)
    all need table-owner/superuser privilege, which the restricted runtime
    role deliberately doesn't have. ``app_database_url``, when given, is a
    *different* connection (the least-privilege ``substrate_app`` role —
    see ``rls.py``) that the returned ``session_factory`` actually uses to
    serve requests; omitted, it falls back to ``database_url`` — today's
    single-connection behavior (RLS stays enabled either way, just inert
    against a superuser connection).

    Returns ``(engine, session_factory)`` for the caller to store on
    ``app.state.*``.  No module-level globals are used.
    """
    admin_engine = create_async_engine(database_url, echo=echo)
    async with admin_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, col, defn in _MIGRATE_COLUMNS:
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {defn}")
            )
        # Enabling the policies is always safe (idempotent DDL, and inert
        # against a superuser connection — see rls.py's module docstring).
        # Only actually provision the dedicated low-privilege role when a
        # password is configured, since that's a persistent DB role, not
        # just column/policy metadata.
        await enable_row_level_security(conn)
        if rls_app_role_password:
            await ensure_app_role(conn, password=rls_app_role_password)

    if app_database_url and app_database_url != database_url:
        await admin_engine.dispose()
        engine = create_async_engine(
            app_database_url,
            echo=echo,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )
    else:
        engine = admin_engine

    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, session_factory


@asynccontextmanager
async def system_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """A session for work the server does on its own, with no signed-in user (a scheduled task firing, the startup load of schedules).

    Row-level security hides every tenant's rows from a connection that has not said which tenant it is acting for, so such a job would
    find nothing, not even the task it was started for. This session acts for the platform: it bypasses the policies, and so must only be
    used by code that picks the rows it touches itself. It holds one connection for its whole life (as ``get_db`` does), because the setting
    lives on the connection and a commit would otherwise hand the session a different one."""
    engine = factory.kw["bind"]
    async with engine.connect() as conn:
        async with factory(bind=conn) as session:
            await session.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            try:
                yield session
            finally:
                try:
                    await session.rollback()
                    await session.execute(
                        text("SELECT set_config('app.bypass_rls', '', false)")
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001 — the connection is closing; do not mask the job's own error
                    pass


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async session from ``app.state``.

    Explicitly binds the session to ONE checked-out ``Connection`` for the
    whole request, rather than letting the session pull from the engine's
    pool per-transaction (the default). This matters specifically for
    ``get_tenant_scoped_db`` (rls_deps.py): a route that calls
    ``db.commit()`` more than once mid-request (e.g. upload_file's
    ``_ensure_user``, then again after the file_metadata insert) was
    silently losing its RLS session GUCs (``app.current_tenant_id`` etc.)
    on the second commit — `Session.commit()` returns the connection to
    the pool by default, so the next statement (e.g. `db.refresh()`) could
    be handed a *different* physical connection that never had
    `set_config` run on it, making the row it just inserted invisible to
    itself under RLS. Verified by a live diagnostic: `current_setting(...)`
    read back as `''` immediately after a commit that had correctly set it
    moments earlier. Pinning one connection for the request's lifetime
    removes the "which physical connection am I on" question entirely.
    """
    engine: AsyncEngine = request.app.state.engine
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with engine.connect() as conn:
        async with factory(bind=conn) as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
