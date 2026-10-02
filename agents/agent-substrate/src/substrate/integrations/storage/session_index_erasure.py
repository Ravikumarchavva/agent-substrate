"""Erasure for the per-user session-document index (vector chunks, PageIndex trees and knowledge graph — see
``integrations/knowledge/session_ingest.py``).

Each user's index is a store of its own, in a folder (``SESSION_INDEX_LOCAL_PATH/tenants/<tenant>/users/<user>/index``),
with the vectors, the trees and the graph all in it. Erasing a user is therefore removing that folder — nothing of the
user's index lives anywhere else — and erasing a tenant is removing the tenant's folder, which also reaches a user whose
only trace is an uploaded document and who has no surviving row elsewhere.

Not covered by ``integrations/gdpr/eraser.py``'s object-storage prefix delete: that sweeps whatever ``ctx.file_store``
points at, and this tree is a separate one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from substrate.workspace.layout import tenant_prefix, user_index_prefix


async def erase_session_index(cfg: Any, tenant_id: str, user_id: str) -> int:
    """Erase one user's whole per-user session-document index. Returns 1 if a folder was removed, 0 if there was nothing
    to erase."""
    path = Path(cfg.SESSION_INDEX_LOCAL_PATH) / user_index_prefix(tenant_id, user_id)
    if not path.exists():
        return 0
    shutil.rmtree(path, ignore_errors=True)
    return 1


async def erase_session_index_for_tenant(cfg: Any, tenant_id: str, known_user_ids: set[str]) -> int:
    """Erase every user's session-document index under one tenant, then the tenant's folder itself.

    ``known_user_ids`` (from the caller's own Thread rows — see ``integrations/gdpr/eraser.py::erase_tenant``) are erased
    explicitly; any other user folder the tenant has is erased too. Returns the number of user indexes removed.
    """
    erased = 0
    for user_id in known_user_ids:
        erased += await erase_session_index(cfg, tenant_id, user_id)

    tenant_dir = Path(cfg.SESSION_INDEX_LOCAL_PATH) / tenant_prefix(tenant_id)
    users_dir = tenant_dir / "users"
    if users_dir.is_dir():
        erased += sum(1 for p in users_dir.iterdir() if p.is_dir() and p.name not in known_user_ids)
    shutil.rmtree(tenant_dir, ignore_errors=True)
    return erased


__all__ = ["erase_session_index", "erase_session_index_for_tenant"]
