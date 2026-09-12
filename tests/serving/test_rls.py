"""Row-Level Security — proves policies actually enforce isolation.

Runs against a real connection as the ``substrate_app`` role (created by
``rls.ensure_app_role``, provisioned here if missing), not the superuser
``DATABASE_URL`` connection every other test uses — RLS never applies to a
superuser at all (see rls.py's module docstring), so a test against that
connection would pass even if every policy were deleted.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from substrate.serving.monolith.rls import APP_DB_ROLE, ensure_app_role

# Reuses whatever RLS_APP_ROLE_PASSWORD the environment already has (the
# same value the real app's own init_db() call provisions the role with) —
# a different literal here would win the last ALTER ROLE and silently
# invalidate the live app's own connection to this role.
_APP_ROLE_PASSWORD = os.environ.get("RLS_APP_ROLE_PASSWORD", "test-rls-role-password")


def _app_role_url(database_url: str) -> str:
    # Swap the credentials only — same host/port/db as the superuser URL
    # every other fixture uses.
    scheme, rest = database_url.split("://", 1)
    _creds, host_part = rest.split("@", 1)
    return f"{scheme}://{APP_DB_ROLE}:{_APP_ROLE_PASSWORD}@{host_part}"


@pytest.fixture
async def app_role_engine(database_url: str):
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await ensure_app_role(conn, password=_APP_ROLE_PASSWORD)
    await engine.dispose()

    role_engine = create_async_engine(_app_role_url(database_url))
    yield role_engine
    await role_engine.dispose()


@pytest.mark.requires_postgres
async def test_role_is_not_a_superuser_and_does_not_bypass_rls(app_role_engine):
    """The precondition every other assertion here depends on — if this
    ever comes back true, RLS is silently inert again (see rls.py)."""
    async with app_role_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"
                ),
                {"r": APP_DB_ROLE},
            )
        ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False


@pytest.mark.requires_postgres
async def test_no_tenant_guc_set_sees_nothing(app_role_engine, database_url):
    tenant_a, tenant_b = f"t-{uuid.uuid4()}", f"t-{uuid.uuid4()}"
    admin_engine = create_async_engine(database_url)
    thread_id = uuid.uuid4()
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO threads (id, name, tenant_id) "
                    "VALUES (:id, :name, :tenant)"
                ),
                {"id": thread_id, "name": "isolated", "tenant": tenant_a},
            )

        async with app_role_engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT id FROM threads WHERE id = :id"), {"id": thread_id}
                )
            ).all()
            assert rows == []
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text("DELETE FROM threads WHERE id = :id"), {"id": thread_id})
        await admin_engine.dispose()
    del tenant_b  # unused here, kept for symmetry with the next test


@pytest.mark.requires_postgres
async def test_tenant_guc_scopes_visibility_to_that_tenant_only(
    app_role_engine, database_url
):
    tenant_a, tenant_b = f"t-{uuid.uuid4()}", f"t-{uuid.uuid4()}"
    admin_engine = create_async_engine(database_url)
    thread_a, thread_b = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO threads (id, name, tenant_id) VALUES "
                    "(:id_a, 'a', :tenant_a), (:id_b, 'b', :tenant_b)"
                ),
                {
                    "id_a": thread_a,
                    "tenant_a": tenant_a,
                    "id_b": thread_b,
                    "tenant_b": tenant_b,
                },
            )

        async with app_role_engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :t, false)"),
                {"t": tenant_a},
            )
            ids = {
                row.id
                for row in (
                    await conn.execute(
                        text("SELECT id FROM threads WHERE id IN (:a, :b)"),
                        {"a": thread_a, "b": thread_b},
                    )
                ).all()
            }
            assert ids == {thread_a}
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM threads WHERE id IN (:a, :b)"),
                {"a": thread_a, "b": thread_b},
            )
        await admin_engine.dispose()


@pytest.mark.requires_postgres
async def test_bypass_rls_guc_sees_every_tenant(app_role_engine, database_url):
    tenant_a, tenant_b = f"t-{uuid.uuid4()}", f"t-{uuid.uuid4()}"
    admin_engine = create_async_engine(database_url)
    thread_a, thread_b = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO threads (id, name, tenant_id) VALUES "
                    "(:id_a, 'a', :tenant_a), (:id_b, 'b', :tenant_b)"
                ),
                {
                    "id_a": thread_a,
                    "tenant_a": tenant_a,
                    "id_b": thread_b,
                    "tenant_b": tenant_b,
                },
            )

        async with app_role_engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.bypass_rls', 'on', false)")
            )
            ids = {
                row.id
                for row in (
                    await conn.execute(
                        text("SELECT id FROM threads WHERE id IN (:a, :b)"),
                        {"a": thread_a, "b": thread_b},
                    )
                ).all()
            }
            assert ids == {thread_a, thread_b}
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM threads WHERE id IN (:a, :b)"),
                {"a": thread_a, "b": thread_b},
            )
        await admin_engine.dispose()


@pytest.mark.requires_postgres
async def test_join_based_policy_scopes_elements_via_their_thread(
    app_role_engine, database_url
):
    """elements has no tenant_id of its own — the policy resolves it
    through thread_id -> threads.tenant_id (see rls.py's _JOIN_POLICY)."""
    tenant_a, tenant_b = f"t-{uuid.uuid4()}", f"t-{uuid.uuid4()}"
    admin_engine = create_async_engine(database_url)
    thread_a, thread_b = uuid.uuid4(), uuid.uuid4()
    elem_a, elem_b = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO threads (id, name, tenant_id) VALUES "
                    "(:id_a, 'a', :tenant_a), (:id_b, 'b', :tenant_b)"
                ),
                {
                    "id_a": thread_a,
                    "tenant_a": tenant_a,
                    "id_b": thread_b,
                    "tenant_b": tenant_b,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO elements (id, thread_id, name) VALUES "
                    "(:eid_a, :id_a, 'a.txt'), (:eid_b, :id_b, 'b.txt')"
                ),
                {"eid_a": elem_a, "id_a": thread_a, "eid_b": elem_b, "id_b": thread_b},
            )

        async with app_role_engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :t, false)"),
                {"t": tenant_a},
            )
            ids = {
                row.id
                for row in (
                    await conn.execute(
                        text("SELECT id FROM elements WHERE id IN (:a, :b)"),
                        {"a": elem_a, "b": elem_b},
                    )
                ).all()
            }
            assert ids == {elem_a}
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM elements WHERE id IN (:a, :b)"),
                {"a": elem_a, "b": elem_b},
            )
            await conn.execute(
                text("DELETE FROM threads WHERE id IN (:a, :b)"),
                {"a": thread_a, "b": thread_b},
            )
        await admin_engine.dispose()
