"""Postgres Row-Level Security — tenant isolation as a DB-enforced backstop.

Defense-in-depth, not the sole enforcement: every route already checks
ownership explicitly (``thread_service.get_owned_thread``,
``routes/files.py::_may_access``, etc.). RLS means a bug in one of those
checks can no longer leak another tenant's row even if the app-layer check
is wrong or missing — the database itself refuses to return it.

Critical prerequisite this module cannot fix by itself: RLS **never**
applies to a superuser, and applies to a table's owning role only when that
table has ``FORCE ROW LEVEL SECURITY`` set (which this module does set, but
still not for a superuser — Postgres exempts superusers unconditionally).
The default local/dev ``DATABASE_URL`` connects as the ``postgres`` role,
which Postgres's own docker image makes a superuser — under that
connection, every policy below is enabled but inert. ``ensure_app_role``
creates a real least-privilege role for the app to connect as instead;
switching ``DATABASE_URL``/``ASYNC_DATABASE_URL`` to it is what actually
activates enforcement, and is deliberately not done automatically by this
module — that's a deployment/credentials change, not a schema migration.

Scope: tenant-level isolation only (``tenant_id`` / ``org_id``), not also
per-user-within-a-tenant — multiple users can legitimately share a tenant
(a deployed chatbot's visitors, a project's KB documents), and per-user
ownership within a tenant already has its own app-layer checks
(``Thread.user_identifier == claims.sub``, etc.) that don't need a second,
stricter DB-level copy. The tables here are the ones with a real
`tenant_id`/`org_id` column or a direct FK to one that carries it
(``threads``, ``elements``, ``feedbacks``, ``file_metadata``,
``file_versions``, ``scheduled_tasks``, ``scheduled_task_runs``,
``workspace_quotas``). Tables with no ownership column at all today
(``pipelines``, ``pipeline_runs``, ``adapter_pipelines``) are out of scope
— adding ownership to those is a separate product decision, not a policy
this module can retrofit.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

APP_DB_ROLE = "substrate_app"

# GUCs the app sets per request (see security/rls_deps.py):
#   app.current_tenant_id — the caller's tenant; rows outside it are hidden.
#   app.bypass_rls        — set to 'on' for admin/service-identity requests,
#                            which already passed their own app-layer check
#                            (require_admin / require_service_identity) and
#                            legitimately need cross-tenant visibility.
# Legacy rows with tenant_id IS NULL stay visible to everyone (matching
# thread_service.get_owned_thread's existing claim-on-first-access
# behavior — RLS must not hide a row from the very request that would
# otherwise claim it).
_TENANT_COLUMN_POLICY = """
USING (
    {column} = current_setting('app.current_tenant_id', true)
    OR {column} IS NULL
    OR current_setting('app.bypass_rls', true) = 'on'
)
"""

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

# (table, policy USING clause) pairs, applied in order — parents before
# children doesn't matter for policy creation itself, only for the join
# policies' own correctness at query time.
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
    # user_id here actually holds a tenant_id (see WorkspaceQuota's
    # docstring) — same column-name-vs-meaning mismatch, kept to avoid a
    # rename migration.
    ("workspace_quotas", _TENANT_COLUMN_POLICY.format(column="user_id")),
]

_POLICY_NAME = "tenant_isolation"


async def enable_row_level_security(conn: AsyncConnection) -> None:
    """Enable + force RLS and (re)create the tenant-isolation policy on
    every in-scope table. Idempotent — safe to run on every startup, same
    as ``database.py``'s ``_MIGRATE_COLUMNS`` loop."""
    for table, using_clause in _POLICIES:
        await conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        await conn.execute(text(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}"))
        await conn.execute(
            text(f"CREATE POLICY {_POLICY_NAME} ON {table} {using_clause}")
        )


async def ensure_app_role(conn: AsyncConnection, *, password: str) -> None:
    """Create the least-privilege role the app should connect as for RLS to
    have any real effect (see module docstring — the default ``postgres``
    connection is a superuser and bypasses RLS unconditionally). Idempotent.

    Does **not** switch the running app over to it — that means changing
    ``DATABASE_URL``/``ASYNC_DATABASE_URL``, a deployment/credentials
    change outside this function's job.
    """
    exists = (
        await conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_DB_ROLE},
        )
    ).scalar_one_or_none()
    # CREATE/ALTER ROLE is DDL — Postgres doesn't accept a bind parameter for
    # the password literal there, only for DML. `password` comes from server
    # config (RLS_APP_ROLE_PASSWORD), not request input; quote_literal-style
    # escaping (doubling embedded single quotes) is the standard safe way to
    # inline a trusted server-side value into DDL text.
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
    # CREATE, not just USAGE: several capabilities create (and, on later
    # startups, ALTER) their own tables lazily at first use over the app's
    # regular connection (task_manager's substrate_task_lists,
    # DurableMemoryStore/DurableSessionStore, pgvector_store, age_store —
    # each does its own `CREATE TABLE IF NOT EXISTS`/`ALTER TABLE ADD
    # COLUMN IF NOT EXISTS`) rather than through this module's bootstrap.
    await conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_DB_ROLE}"))
    # Reassign every table/sequence the admin/bootstrap connection
    # currently owns to the app role — ALTER TABLE and similar DDL need
    # real ownership, not just SELECT/INSERT/UPDATE/DELETE grants, and
    # those lazy self-migrations above have no other connection to run as.
    # Safe for the RLS-covered tables specifically because
    # `enable_row_level_security` sets FORCE ROW LEVEL SECURITY, which
    # (unlike a plain ENABLE) still applies to a table's own owner, not
    # just other roles. Table-by-table `ALTER ... OWNER TO`, not a blanket
    # `REASSIGN OWNED BY` — that also tries to reassign objects Postgres
    # itself won't let move (e.g. extension-owned objects), and fails the
    # whole statement rather than skipping them.
    admin_role = (await conn.execute(text("SELECT current_user"))).scalar_one()
    if admin_role != APP_DB_ROLE:
        owned = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relkind FROM pg_class c "
                    "WHERE c.relnamespace = 'public'::regnamespace "
                    "AND c.relkind IN ('r', 'S') "
                    "AND pg_get_userbyid(c.relowner) = :role "
                    # Exclude a SERIAL/IDENTITY column's own sequence — it's
                    # auto-owned by its table (pg_depend deptype 'a') and
                    # Postgres refuses a direct ALTER ... OWNER on it;
                    # reassigning the table already carries the sequence.
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
    # SERIAL/IDENTITY columns (e.g. the durable-runtime event log's
    # substrate_signals_id_seq) need explicit sequence USAGE — table grants
    # alone don't cover nextval().
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
    # CREATE EXTENSION IF NOT EXISTS runs at startup (pgvector_store.py,
    # age_store.py) — a no-op once the extension is already installed
    # (confirmed: succeeds as a non-superuser when it pre-exists), but
    # grant CREATE on the database too so a *trusted* extension (pgvector
    # is) can still be installed fresh by this role if a deployment's DB
    # doesn't have it yet. Untrusted extensions (Apache AGE) still need a
    # superuser to install once, same as today — this grant doesn't change
    # that, only removes the unnecessary blanket superuser requirement for
    # the common case.
    db_name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    await conn.execute(text(f'GRANT CREATE ON DATABASE "{db_name}" TO {APP_DB_ROLE}'))
