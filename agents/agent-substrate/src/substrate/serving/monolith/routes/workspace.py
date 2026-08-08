"""Workspace storage management — usage, listing, and deletion for the
per-user filesystem backing uploads and code-interpreter artifacts.

Works against any file store that can enumerate a user's files — both
``WorkspaceFileStore`` (``FILE_STORE_BACKEND=local``, a filesystem tree) and
``S3FileStore`` (``=s3``, object storage keyed on the same ``users/{id}/...``
layout) qualify. Stores that can't, like ``InMemoryFileStore``, 501 here.

Routes:
  GET    /workspace/usage   – bytes used vs. quota for the caller
  GET    /workspace/files   – files grouped by session (thread)
  DELETE /workspace/files   – delete one file by its workspace-relative path
"""

from __future__ import annotations

import mimetypes
import uuid
from typing import Protocol, runtime_checkable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.capabilities.storage.workspace import WorkspacePathError
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.file_versioning import (
    VERSIONS_DIR,
    capture_bytes,
    latest_version,
    list_versions,
    record_version,
    sha256_hex,
)
from substrate.serving.monolith.models import FileMetadata, FileVersion, Thread
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
    owner: str  # "user" (uploaded) | "agent" (assistant-created)


class WorkspaceFilesResponse(BaseModel):
    files: list[WorkspaceFileEntry]


@runtime_checkable
class _WorkspaceCapableStore(Protocol):
    """The surface this API needs beyond plain upload/download/delete.

    A capability check rather than ``isinstance(WorkspaceFileStore)``: both
    ``WorkspaceFileStore`` (filesystem tree) and ``S3FileStore`` (object
    storage, keyed on the same ``users/{id}/...`` layout) implement it, and the
    backend is meant to be swappable without touching this API. Stores that
    can't enumerate a user's files — ``InMemoryFileStore`` — still get a 501.
    """

    async def exists(self, key: str) -> bool: ...
    async def usage_bytes(self, user_id: str, *, force: bool = False) -> int: ...
    async def list_user_files(self, user_id: str) -> list[tuple[str, int, float]]: ...
    async def download(self, key: str) -> bytes: ...
    async def upload(
        self, key: str, data: bytes, *, content_type: str = ...
    ) -> None: ...
    async def delete(self, key: str) -> None: ...


def _require_workspace_store(ctx: ServerDependencies) -> _WorkspaceCapableStore:
    store = ctx.file_store
    if not isinstance(store, _WorkspaceCapableStore):
        raise HTTPException(
            status_code=501,
            detail=(
                "Workspace management requires a file store that can enumerate "
                "a user's files (FILE_STORE_BACKEND=local or s3)."
            ),
        )
    return store


def _is_version_key(key: str) -> bool:
    """True for a snapshot under ``users/{uid}/versions/...``.

    Anchored at the owner prefix rather than matching ``/versions/`` anywhere:
    a user is perfectly entitled to a folder of their own called ``versions``,
    and it must not vanish from their file list.
    """
    parts = key.split("/")
    return len(parts) >= 3 and parts[0] == "users" and parts[2] == VERSIONS_DIR


def _session_id_from_key(key: str) -> str | None:
    parts = key.split("/")
    # users/{uid}/sessions/{thread_id}/...
    if len(parts) >= 4 and parts[0] == "users" and parts[2] == "sessions":
        return parts[3]
    return None


def _is_inline_type(content_type: str) -> bool:
    """Types browsers can render inline — everything else forces a download.

    Mirrors routes/files.py::_is_previewable so the same endpoint backs both
    an inline <img> and a click-to-download link (see the frontend
    ``sandbox:`` markdown resolver).
    """
    return (
        content_type.startswith("image/")
        or content_type.startswith("text/")
        or content_type == "application/pdf"
    )


@router.get("/usage", response_model=WorkspaceUsageResponse)
async def get_usage(
    claims: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
) -> WorkspaceUsageResponse:
    store = _require_workspace_store(ctx)
    used = await store.usage_bytes(claims.sub, force=True)
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
    # Hide the per-file version snapshots — they're internal history, not
    # user-facing files (see file_versioning.py).
    entries = [
        e for e in await store.list_user_files(claims.sub) if not _is_version_key(e[0])
    ]
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

    # A file is user-owned iff it has a (non-deleted) FileMetadata row: uploads
    # go through routes/files.py which records one, while code-interpreter /
    # assistant artifacts are written straight to the session dir with none.
    keys = [key for key, _, _ in entries]
    uploaded_keys: set[str] = set()
    if keys:
        meta_rows = (
            await db.execute(
                select(FileMetadata.object_key).where(
                    FileMetadata.object_key.in_(keys),
                    FileMetadata.deleted_at.is_(None),
                )
            )
        ).all()
        uploaded_keys = {row.object_key for row in meta_rows}

    files = [
        WorkspaceFileEntry(
            path=key,
            name=key.rsplit("/", 1)[-1],
            size_bytes=size,
            modified_at=mtime,
            session_id=session_ids_by_key[key],
            session_name=thread_names.get(session_ids_by_key[key] or ""),
            owner="user" if key in uploaded_keys else "agent",
        )
        for key, size, mtime in entries
    ]
    return WorkspaceFilesResponse(files=files)


def _session_key(sub: str, thread_id: str, path: str) -> str:
    """Build (and validate) the ownership-scoped canonical key for a
    session-relative path."""
    rel = path.lstrip("/")
    if ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    return f"users/{sub}/sessions/{thread_id}/{rel}"


async def _resolve_session_key(
    store: _WorkspaceCapableStore, sub: str, thread_id: str, path: str
) -> str:
    """Resolve a session-relative ref to the real object key of an existing file.

    The model sometimes references a generated file by bare name
    (``report.pptx``) even though its code wrote it into a subdirectory of the
    run cwd (``out/report.pptx``). Prefer the exact path; if it doesn't exist,
    fall back to the most-recently-modified file with the same basename anywhere
    under the session dir (excluding version snapshots). When nothing matches,
    return the exact key so writes to a brand-new file still land where asked.
    """
    exact = _session_key(sub, thread_id, path)
    if await store.exists(exact):
        return exact
    base = path.rsplit("/", 1)[-1]
    prefix = f"users/{sub}/sessions/{thread_id}/"
    matches = [
        (key, mtime)
        for (key, _size, mtime) in await store.list_user_files(sub)
        if key.startswith(prefix)
        and not _is_version_key(key)
        and key.rsplit("/", 1)[-1] == base
    ]
    if matches:
        matches.sort(key=lambda item: item[1], reverse=True)
        return matches[0][0]
    return exact


def _session_rel(key: str, sub: str, thread_id: str) -> str:
    """Inverse of `_session_key`: the session-relative path for a resolved key."""
    prefix = f"users/{sub}/sessions/{thread_id}/"
    return key[len(prefix) :] if key.startswith(prefix) else key


# Only editable documents are worth versioning. Images embedded in an HTML
# report are fetched through serve_file too, but the user never edits them —
# skip them so we don't snapshot a version per chart.
_NON_VERSIONABLE_EXTS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "bmp",
    "ico",
    "avif",
}


def _is_versionable(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext not in _NON_VERSIONABLE_EXTS


class WorkspaceVersionEntry(BaseModel):
    seq: int
    author: str
    checksum_sha256: str
    size_bytes: int
    created_at: float
    restored_from_seq: int | None = None


class WorkspaceVersionsResponse(BaseModel):
    versions: list[WorkspaceVersionEntry]
    latest_seq: int | None


class RestoreVersionRequest(BaseModel):
    thread_id: str
    path: str
    seq: int


@router.get("/file")
async def serve_file(
    thread_id: str = Query(..., description="Conversation/thread id (session folder)"),
    path: str = Query(
        ..., description="File path relative to the thread's session dir"
    ),
    seq: int | None = Query(
        None, description="Serve a specific version (default: latest)"
    ),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Serve a code-interpreter / workspace file by its session-relative path.

    Resolves to ``users/{caller}/sessions/{thread_id}/{path}`` — the same
    per-thread directory the sandbox runs in (see the code-interpreter tools'
    ``workspace_dir``). This is what the frontend ``sandbox:<path>`` markdown
    refs resolve to: images render inline, everything else downloads.

    Serving the *current* file also lazily versions it: a change made outside
    our save endpoints (i.e. the agent rewrote it via code_interpreter) is
    captured as an ``"agent"`` ``FileVersion`` here, so the panel's version
    history stays honest without a per-turn scan.
    """
    store = _require_workspace_store(ctx)
    key = await _resolve_session_key(store, claims.sub, thread_id, path)

    if seq is not None:
        version = (
            await db.execute(
                select(FileVersion).where(
                    FileVersion.object_key == key, FileVersion.seq == seq
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise HTTPException(status_code=404, detail="Version not found")
        source_key = version.version_key
    else:
        source_key = key

    try:
        data = await store.download(source_key)
    except (WorkspacePathError, KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="File not found") from None

    name = key.rsplit("/", 1)[-1]
    checksum = sha256_hex(data)
    if seq is None and _is_versionable(name):
        # Capture an out-of-band (agent) change / the initial state.
        await capture_bytes(
            db,
            store,
            object_key=key,
            data=data,
            user_id=claims.sub,
            thread_id=thread_id,
        )

    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    disposition = "inline" if _is_inline_type(content_type) else "attachment"

    async def _stream():
        yield data

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "X-File-Checksum": checksum,
        },
    )


@router.put("/file", status_code=200)
async def save_file(
    request: Request,
    thread_id: str = Query(...),
    path: str = Query(...),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    """Save edited bytes back to a workspace file (used by text/Monaco editors;
    ONLYOFFICE Office edits arrive via its own callback in M3).

    Optimistic concurrency: the client sends the checksum it loaded in
    ``X-Base-Checksum``; if the canonical file changed since (the agent wrote
    it), respond 409 so the UI can offer reload-vs-overwrite. The prior state
    is already versioned (via serve_file's lazy capture), so nothing is lost.
    """
    store = _require_workspace_store(ctx)
    key = await _resolve_session_key(store, claims.sub, thread_id, path)
    body = await request.body()
    base = request.headers.get("X-Base-Checksum", "")

    current: bytes = b""
    current_checksum = ""
    try:
        current = await store.download(key)
        current_checksum = sha256_hex(current)
    except (WorkspacePathError, KeyError, FileNotFoundError):
        pass

    if base and current_checksum and base != current_checksum:
        latest = await latest_version(db, key)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "File changed since you opened it.",
                "current_checksum": current_checksum,
                "latest_seq": latest.seq if latest else None,
            },
        )

    # Ensure the pre-save state is versioned, then write + version the new one.
    if current_checksum:
        await capture_bytes(
            db,
            store,
            object_key=key,
            data=current,
            user_id=claims.sub,
            thread_id=thread_id,
        )
    await store.upload(key, body)
    version = await record_version(
        db,
        store,
        object_key=key,
        data=body,
        author="user",
        user_id=claims.sub,
        thread_id=thread_id,
    )
    return {"checksum": version.checksum_sha256, "seq": version.seq}


@router.get("/versions", response_model=WorkspaceVersionsResponse)
async def get_versions(
    thread_id: str = Query(...),
    path: str = Query(...),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> WorkspaceVersionsResponse:
    store = _require_workspace_store(ctx)
    key = await _resolve_session_key(store, claims.sub, thread_id, path)
    versions = await list_versions(db, key)
    return WorkspaceVersionsResponse(
        versions=[
            WorkspaceVersionEntry(
                seq=v.seq,
                author=v.author,
                checksum_sha256=v.checksum_sha256,
                size_bytes=v.size_bytes,
                created_at=v.created_at.timestamp() if v.created_at else 0.0,
                restored_from_seq=v.restored_from_seq,
            )
            for v in versions
        ],
        latest_seq=versions[-1].seq if versions else None,
    )


@router.post("/versions/restore", status_code=200)
async def restore_version(
    body: RestoreVersionRequest,
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    """Restore a prior version: copy that snapshot's bytes to the canonical
    file as a new ``"restore"`` version tagged with the seq it came from
    (non-destructive — the current state was already captured, so it stays in
    history too)."""
    store = _require_workspace_store(ctx)
    key = await _resolve_session_key(store, claims.sub, body.thread_id, body.path)
    version = (
        await db.execute(
            select(FileVersion).where(
                FileVersion.object_key == key, FileVersion.seq == body.seq
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    try:
        data = await store.download(version.version_key)
    except (WorkspacePathError, KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Snapshot missing") from None

    # Capture the current state first (so restoring doesn't lose it), then write.
    try:
        current = await store.download(key)
        await capture_bytes(
            db,
            store,
            object_key=key,
            data=current,
            user_id=claims.sub,
            thread_id=body.thread_id,
        )
    except (WorkspacePathError, KeyError, FileNotFoundError):
        pass
    await store.upload(key, data)
    new_version = await record_version(
        db,
        store,
        object_key=key,
        data=data,
        author="restore",
        user_id=claims.sub,
        thread_id=body.thread_id,
        restored_from_seq=body.seq,
    )
    return {"checksum": new_version.checksum_sha256, "seq": new_version.seq}


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
