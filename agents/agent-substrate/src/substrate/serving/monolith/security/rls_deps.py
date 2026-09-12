"""``get_db``, but with the RLS GUCs set for the caller (see ``rls.py``).

Only takes effect once the app actually connects as the low-privilege
``substrate_app`` role (see ``rls.py``'s module docstring) — against the
default superuser connection this is a no-op every policy already tolerates
(``current_setting`` values are simply never consulted, since RLS itself
never applies to a superuser).

Use this instead of plain ``Depends(get_db)`` in routes that read/write
tenant-scoped tables (threads, elements, feedbacks, file_metadata,
file_versions, scheduled_tasks/runs, workspace_quotas) and need the DB-level
backstop, not just the route's own ownership check.
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims


_RESET_GUCS = text(
    "SELECT set_config('app.current_tenant_id', '', false), "
    "set_config('app.bypass_rls', '', false)"
)


async def _reset_gucs(db: AsyncSession) -> None:
    """Best-effort — if the session is already unusable (e.g. the route
    raised and the transaction needs a rollback get_db hasn't run yet), a
    failed reset here isn't fatal: it doesn't mask the real error, and
    ``pool_pre_ping``/connection invalidation on a genuinely broken
    connection makes it unlikely to be handed to an unrelated request
    still holding a stale GUC. Not a substitute for keeping this cheap
    reset actually running in the common case, which is why it's here at
    all rather than relied upon as the sole backstop."""
    try:
        await db.execute(_RESET_GUCS)
    except Exception:
        pass


async def get_tenant_scoped_db(
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AsyncGenerator[AsyncSession, None]:
    # set_config(..., false) — session-scoped, not transaction-local
    # (`true`/SET LOCAL): several routes call `db.commit()` themselves
    # mid-request (e.g. upload_file's _ensure_user, then again after the
    # insert), and a transaction-local GUC is cleared the moment any of
    # those commits ends its transaction — silently blinding every query
    # for the rest of the request. Session-scoped survives that, but must
    # be explicitly reset in `finally` before the underlying connection
    # goes back to the pool, or it would leak into whatever unrelated
    # request borrows that connection next.
    if claims.is_admin or claims.token_type == "service":
        # Already passed its own app-layer check (require_admin /
        # require_service_identity) and legitimately needs cross-tenant
        # visibility — e.g. the admin storage API, GDPR erasure.
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
    else:
        await db.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, false)"),
            {"tid": claims.tenant_id},
        )
    try:
        yield db
    finally:
        await _reset_gucs(db)


async def get_service_scoped_db(
    db: AsyncSession = Depends(get_db),
) -> AsyncGenerator[AsyncSession, None]:
    """For routes already gated by ``require_admin``/
    ``require_service_identity`` at the route level — those checks are the
    real authorization; this only lifts the DB-level tenant filter so the
    (already-verified) admin/service caller can see across tenants, same as
    ``get_tenant_scoped_db``'s bypass branch, without needing this route to
    also depend on ``get_current_user``."""
    await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
    try:
        yield db
    finally:
        await _reset_gucs(db)
