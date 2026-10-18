# Tenant Isolation & Row-Level Security (RLS)

This document describes the PostgreSQL Row-Level Security (RLS) architecture used in Agent Substrate to guarantee multi-tenant data isolation at the database level.

---

## 1. Why DB-Enforced RLS?

Agent Substrate enforces multi-tenancy in two layers:

1. **Application Layer**: Every API route and service explicitly checks ownership (e.g., `thread_service.get_owned_thread`, `routes/files.py::_may_access`).
2. **Database Layer (RLS)**: PostgreSQL Row-Level Security acts as a defense-in-depth backstop. If an application bug, refactoring regression, or missed query check occurs, PostgreSQL itself refuses to return or mutate rows belonging to another tenant.

---

## 2. PostgreSQL Role Separation & Privileges

PostgreSQL has a critical invariant regarding RLS: **Superusers unconditionally bypass RLS policies.**

In local development and default container deployments, `DATABASE_URL` connects as the default `postgres` user, which is a superuser. Under this user, RLS policies are enabled but completely inert.

To activate real isolation:
- `ensure_app_role(conn, password=...)` idempotently creates a dedicated, unprivileged role: `substrate_app` (`NOSUPERUSER NOBYPASSRLS`).
- All application tables and sequences owned by the bootstrap/admin connection are reassigned to `substrate_app`.
- Table policies are enforced using `ALTER TABLE ... FORCE ROW LEVEL SECURITY`, which ensures that policies apply even to the table owner.
- Deployments switch `APP_DATABASE_URL` (or `ASYNC_DATABASE_URL`) to connect as `substrate_app`.

### Schema & Extension Permissions
- `GRANT USAGE, CREATE ON SCHEMA public TO substrate_app`: Allows capabilities (e.g., `pgvector_store`, `task_manager`, `DurableMemoryStore`) to safely run lazy `CREATE TABLE IF NOT EXISTS` or `ALTER TABLE ADD COLUMN IF NOT EXISTS` during startup.
- `GRANT USAGE, SELECT ON ALL SEQUENCES`: Ensures `SERIAL` / `IDENTITY` primary keys function with `nextval()`.
- `GRANT CREATE ON DATABASE`: Allows unprivileged creation of trusted extensions (e.g., `pgvector`). Untrusted extensions (such as Apache AGE) still require superuser initialization once during database provisioning.

---

## 3. Session GUCs & Policy Design

RLS policies evaluate runtime configuration parameters (GUCs) set per request (see `serving/monolith/security/rls_deps.py`):

| GUC Parameter | Purpose |
|---|---|
| `app.current_tenant_id` | Set to the caller's verified `tenant_id` (from JWT claims). Rows outside this tenant are hidden. |
| `app.bypass_rls` | Set to `'on'` for administrative or service-identity requests that have passed explicit authorization and require cross-tenant visibility. |

### Policy Logic
- **Direct Tenant Column (`threads`, `file_metadata`, `file_versions`, `workspace_quotas`)**:
  ```sql
  USING (
      tenant_id = current_setting('app.current_tenant_id', true)
      OR tenant_id IS NULL
      OR current_setting('app.bypass_rls', true) = 'on'
  )
  ```
  *Note*: Legacy rows where `tenant_id IS NULL` remain visible to allow claim-on-first-access migration without locking out existing users.
- **Parent Join Policies (`elements`, `feedbacks`, `scheduled_tasks`, `scheduled_task_runs`)**:
  Enforced by querying parent thread ownership:
  ```sql
  USING (
      thread_id IN (
          SELECT id FROM threads
          WHERE tenant_id = current_setting('app.current_tenant_id', true)
             OR tenant_id IS NULL
      )
      OR current_setting('app.bypass_rls', true) = 'on'
  )
  ```

---

## 4. Scope & Boundaries

- **Tenant Isolation**: Covers organizational and tenant boundaries (`tenant_id` / `org_id`).
- **User Ownership**: Per-user boundaries within a single tenant (e.g., user A vs user B inside the same organization) are handled at the application layer (`Thread.user_identifier == claims.sub`), allowing legitimate shared tenant resources (e.g., common knowledge base documents).

