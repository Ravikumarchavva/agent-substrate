"""Workspace storage management — usage, listing, and deletion for the
per-user filesystem backing uploads and code-interpreter artifacts.

Only meaningful when ``ctx.file_store`` is a ``WorkspaceFileStore``
(``FILE_STORE_BACKEND=local``, the default — see
``capabilities/storage/workspace.py``). Under other backends (s3/memory)
these routes 501, since usage/listing/delete-by-path have no equivalent
there today.

Routes:
  GET    /workspace/usage   – bytes used vs. quota for the caller
  GET    /workspace/files   – files grouped by session (thread)
  DELETE /workspace/files   – delete one file by its workspace-relative path
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.capabilities.storage.workspace import (
    WorkspaceFileStore,
    WorkspacePathError,
)
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import FileMetadata, Thread
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

router = APIRouter(
    prefix="/workspace",
    tags=["workspace"],
    dependencies=[Depends(get_current_user)],
)


class WorkspaceUsageResponse(BaseModel):
    used_bytes: int
    quota_bytes: int


class WorkspaceFileEntry(BaseModel):
    path: str
    name: str
    size_bytes: int
    modified_at: float
    session_id: str | None
    session_name: str | None


class WorkspaceFilesResponse(BaseModel):
    files: list[WorkspaceFileEntry]


def _require_workspace_store(ctx: ServerDependencies) -> WorkspaceFileStore:
    if not isinstance(ctx.file_store, WorkspaceFileStore):
        raise HTTPException(
            status_code=501,
            detail="Workspace management is only available with FILE_STORE_BACKEND=local.",
        )
    return ctx.file_store


def _session_id_from_key(key: str) -> str | None:
    parts = key.split("/")
    # users/{uid}/sessions/{thread_id}/...
    if len(parts) >= 4 and parts[0] == "users" and parts[2] == "sessions":
        return parts[3]
    return None


@router.get("/usage", response_model=WorkspaceUsageResponse)
async def get_usage(
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> WorkspaceUsageResponse:
    store = _require_workspace_store(ctx)
    used = store.usage_bytes(claims.sub, force=True)
    return WorkspaceUsageResponse(
        used_bytes=used, quota_bytes=ctx.workspace_user_quota_bytes
    )


@router.get("/files", response_model=WorkspaceFilesResponse)
async def list_files(
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> WorkspaceFilesResponse:
    store = _require_workspace_store(ctx)
    entries = store.list_user_files(claims.sub)
    session_ids_by_key = {key: _session_id_from_key(key) for key, _, _ in entries}

    valid_uuids: list[uuid.UUID] = []
    for sid in set(session_ids_by_key.values()):
        if sid is None:
            continue
        try:
            valid_uuids.append(uuid.UUID(sid))
        except ValueError:
            continue

    thread_names: dict[str, str | None] = {}
    if valid_uuids:
        rows = (
            await db.execute(
                select(Thread.id, Thread.name).where(Thread.id.in_(valid_uuids))
            )
        ).all()
        thread_names = {str(row.id): row.name for row in rows}

    files = [
        WorkspaceFileEntry(
            path=key,
            name=key.rsplit("/", 1)[-1],
            size_bytes=size,
            modified_at=mtime,
            session_id=session_ids_by_key[key],
            session_name=thread_names.get(session_ids_by_key[key] or ""),
        )
        for key, size, mtime in entries
    ]
    return WorkspaceFilesResponse(files=files)


@router.delete("/files", status_code=204)
async def delete_file(
    path: str = Query(..., description="Workspace-relative file path"),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> None:
    if not ctx.workspace_user_delete_allowed:
        raise HTTPException(
            status_code=403, detail="Storage deletion is disabled for this deployment."
        )
    store = _require_workspace_store(ctx)

    # Ownership check: the path must live under this caller's own tree —
    # WorkspaceFileStore's own traversal guard stops `../` escapes, but
    # doesn't know about ownership, so enforce that here.
    if not path.startswith(f"users/{claims.sub}/"):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        await store.delete(path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = await db.execute(
        select(FileMetadata).where(
            FileMetadata.object_key == path, FileMetadata.deleted_at.is_(None)
        )
    )
    meta = result.scalar_one_or_none()
    if meta is not None:
        from datetime import datetime, timezone

        meta.deleted_at = datetime.now(timezone.utc)
        await db.commit()
