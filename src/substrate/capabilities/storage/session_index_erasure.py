"""Erasure for the per-user session-document index (vector + PageIndex tree
+ knowledge graph — see ``capabilities/knowledge/session_ingest.py``).

Not covered by ``capabilities/gdpr/eraser.py``'s existing object-storage
prefix delete: that sweeps whatever ``ctx.file_store`` points at, but the
Lance data lives somewhere structurally different depending on
``SESSION_INDEX_NAMESPACE_URI``:

* Local-path mode: a *separate* directory tree
  (``SESSION_INDEX_LOCAL_PATH``), not under ``FILE_STORE_ROOT`` at all.
* Namespace-catalog mode (e.g. SeaweedFS's Lance Catalog): its own
  namespace/table registry, reached over the Lance Namespace REST API, not
  the plain S3 prefix ``ctx.file_store`` uses.

Namespace-mode erasure was verified directly against a real Lance Namespace
catalog before writing this, not assumed: **``drop_namespace(...,
behavior="CASCADE")`` is not supported** by this Lance Namespace
implementation — it fails with "cascade drop is not supported; drop the
tables in the namespace first." The working sequence, confirmed end-to-end
(including that a sibling namespace survives untouched): list every table
in the namespace, drop each individually, then drop the now-empty
namespace. ``list_tables`` returns fully-qualified ``$``-joined identifiers
(e.g. ``bucket$tenant$user$vectors``), not bare table names — passing the
qualified name straight to ``drop_table`` fails ("the identifier in the
request body does not match the one in the route"); only the last ``$``
segment is the name ``drop_table`` actually wants.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from substrate.capabilities.storage.layout import user_index_prefix


async def _drop_lance_namespace(db: Any, namespace_path: list[str]) -> int:
    """Drop every table in *namespace_path*, then the (now-empty)
    namespace itself. Returns the number of tables dropped. Safe to call on
    a namespace that was never created (nothing was ever ingested) or is
    already gone — both no-op rather than raise, matching
    ``_delete_prefix``'s "erasing something that may not exist" tolerance
    in ``capabilities/gdpr/eraser.py``."""
    try:
        resp = await db.list_tables(namespace_path=namespace_path)
    except Exception:
        return 0
    dropped = 0
    for full_name in resp.tables:
        bare_name = full_name.rsplit("$", 1)[-1]
        await db.drop_table(bare_name, namespace_path=namespace_path)
        dropped += 1
    try:
        await db.drop_namespace(namespace_path)
    except Exception:
        pass  # namespace itself may not exist (e.g. 0 tables, never created)
    return dropped


async def erase_session_index(cfg: Any, tenant_id: str, user_id: str) -> int:
    """Erase one user's entire per-user session-document index (all Lance
    tables: vectors, pageindex_trees, entities, relationships) — the whole
    scope at ``user_index_prefix(tenant_id, user_id)``, not a per-session
    slice (matches this index's own per-user, not per-session, granularity
    — see the storage plan). Returns the number of tables removed
    (namespace mode) or 1 if a local directory was removed, 0 if there was
    nothing to erase.
    """
    if cfg.SESSION_INDEX_NAMESPACE_URI:
        import lancedb

        db = lancedb.connect_namespace_async(
            "rest", {"uri": cfg.SESSION_INDEX_NAMESPACE_URI}
        )
        namespace_path = [cfg.SESSION_INDEX_BUCKET, tenant_id, user_id]
        return await _drop_lance_namespace(db, namespace_path)

    path = Path(cfg.SESSION_INDEX_LOCAL_PATH) / user_index_prefix(tenant_id, user_id)
    if not path.exists():
        return 0
    shutil.rmtree(path, ignore_errors=True)
    return 1


async def erase_session_index_for_tenant(
    cfg: Any, tenant_id: str, known_user_ids: set[str]
) -> int:
    """Erase every user's session-document index under one tenant.

    ``known_user_ids`` (from the caller's own Postgres Thread rows — see
    ``capabilities/gdpr/eraser.py::erase_tenant``) is erased explicitly by
    id; in namespace mode, also enumerates and erases any *other* user
    namespaces this tenant has (belt-and-suspenders — catches a user whose
    only trace left is uploaded documents, with no surviving Thread row),
    then drops the tenant-level namespace itself. Local-path mode has no
    per-tenant directory in the same sense (each user's index lives under
    its own ``tenants/<tid>/users/<uid>/index`` leaf — no shared parent to
    rmtree that wouldn't require walking the tree anyway), so it just loops
    ``known_user_ids``.
    """
    erased = 0
    for user_id in known_user_ids:
        erased += await erase_session_index(cfg, tenant_id, user_id)

    if not cfg.SESSION_INDEX_NAMESPACE_URI:
        return erased

    import lancedb

    db = lancedb.connect_namespace_async("rest", {"uri": cfg.SESSION_INDEX_NAMESPACE_URI})
    tenant_namespace = [cfg.SESSION_INDEX_BUCKET, tenant_id]
    try:
        resp = await db.list_namespaces(tenant_namespace)
    except Exception:
        return erased
    remaining_users = [u for u in resp.namespaces if u not in known_user_ids]
    for user_id in remaining_users:
        erased += await _drop_lance_namespace(db, [*tenant_namespace, user_id])
    try:
        await db.drop_namespace(tenant_namespace)
    except Exception:
        pass
    return erased


__all__ = ["erase_session_index", "erase_session_index_for_tenant"]
