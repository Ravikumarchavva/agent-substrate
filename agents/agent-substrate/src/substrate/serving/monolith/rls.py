"""PostgreSQL Row-Level Security — tenant isolation backstop.

Enforces tenant-level isolation at the database layer as a defense-in-depth
backstop behind application route checks.

For full architectural documentation on role separation, GUCs, table ownership,
and policy designs, see ``docs/claude_docs/architecture/tenant-isolation-rls.md``.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

APP_DB_ROLE = "substrate_app"

# Tenant column policy: filters by app.current_tenant_id or bypass flag
_TENANT_COLUMN_POLICY = """
USING (
    {column} = current_setting('app.current_tenant_id', true)
    OR {column} IS NULL
    OR current_setting('app.bypass_rls', true) = 'on'
)
"""

# Foreign key join policy: inherits tenant filter from parent thread
_JOIN_POLICY = """
USING (
    {fk} IN (
        SELECT id FROM {parent}
        WHERE {parent_column} = current_setting('app.current_tenant_id', true)
           OR {parent_column} IS NULL
    )
    OR current_setting('app.bypass_rls', true) = 'on'
)
"""

_POLICIES: list[tuple[str, str]] = [
    ("threads", _TENANT_COLUMN_POLICY.format(column="tenant_id")),
    (
        "elements",
        _JOIN_POLICY.format(
            fk="thread_id", parent="threads", parent_column="tenant_id"
        ),
    ),
    (
        "feedbacks",
        _JOIN_POLICY.format(
            fk="thread_id", parent="threads", parent_column="tenant_id"
        ),
    ),
    ("file_metadata", _TENANT_COLUMN_POLICY.format(column="org_id")),
    ("file_versions", _TENANT_COLUMN_POLICY.format(column="tenant_id")),
    (
        "scheduled_tasks",
        _JOIN_POLICY.format(
            fk="thread_id", parent="threads", parent_column="tenant_id"
        ),
    ),
    (
        "scheduled_task_runs",
        """
        USING (
            task_id IN (
                SELECT st.id FROM scheduled_tasks st
                JOIN threads t ON t.id = st.thread_id
                WHERE t.tenant_id = current_setting('app.current_tenant_id', true)
                   OR t.tenant_id IS NULL
            )
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """,
    ),
    ("workspace_quotas", _TENANT_COLUMN_POLICY.format(column="user_id")),
]

_POLICY_NAME = "tenant_isolation"


async def enable_row_level_security(conn: AsyncConnection) -> None:
    """Enable and force RLS and (re)create tenant isolation policies on all in-scope tables."""
    for table, using_clause in _POLICIES:
        await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await conn.execute(text(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}"))
        await conn.execute(
            text(f"CREATE POLICY {_POLICY_NAME} ON {table} {using_clause}")
        )


async def ensure_app_role(conn: AsyncConnection, *, password: str) -> None:
    """Create the least-privilege role the app connects as for RLS enforcement.

    Idempotent. Does not change connection strings automatically.
    """
    exists = (
        await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_DB_ROLE},
        )
    ).scalar_one_or_none()

    # Escape quotes for DDL role password
    quoted_password = password.replace("'", "''")
    if exists is None:
        await conn.execute(
            text(
                f"CREATE ROLE {APP_DB_ROLE} LOGIN PASSWORD '{quoted_password}' "
                "NOSUPERUSER NOBYPASSRLS"
            )
        )
    else:
        await conn.execute(
            text(f"ALTER ROLE {APP_DB_ROLE} WITH LOGIN PASSWORD '{quoted_password}'")
        )

    # Allow schema creation for stores that lazily auto-migrate tables
    await conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_DB_ROLE}"))

    # Reassign tables and sequences from admin/bootstrap connection to app role
    admin_role = (await conn.execute(text("SELECT current_user"))).scalar_one()
    if admin_role != APP_DB_ROLE:
        owned = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relkind FROM pg_class c "
                    "WHERE c.relnamespace = 'public'::regnamespace "
                    "AND c.relkind IN ('r', 'S') "
                    "AND pg_get_userbyid(c.relowner) = :role "
                    # Exclude sequence auto-owned by a table (handled with table reassignment)
                    "AND NOT (c.relkind = 'S' AND EXISTS ("
                    "  SELECT 1 FROM pg_depend d "
                    "  WHERE d.objid = c.oid AND d.deptype = 'a'"
                    "))"
                ),
                {"role": admin_role},
            )
        ).all()
        for row in owned:
            kind = "TABLE" if row.relkind == "r" else "SEQUENCE"
            await conn.execute(
                text(f'ALTER {kind} "{row.relname}" OWNER TO {APP_DB_ROLE}')
            )

    await conn.execute(
        text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f"TO {APP_DB_ROLE}"
        )
    )
    await conn.execute(
        text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_DB_ROLE}")
    )
    await conn.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_DB_ROLE}"
        )
    )
    await conn.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_DB_ROLE}"
        )
    )

    # Grant CREATE on DB for self-hosted extensions (e.g. pgvector)
    db_name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    await conn.execute(text(f'GRANT CREATE ON DATABASE "{db_name}" TO {APP_DB_ROLE}'))
