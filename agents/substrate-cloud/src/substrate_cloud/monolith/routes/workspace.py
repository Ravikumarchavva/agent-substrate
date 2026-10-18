"""Workspace storage management — usage, listing, and deletion for the
tenant-scoped filesystem backing uploads and code-interpreter artifacts.

Works against the caller's file store: the data service's Files domain (``DATA_SERVICE_URL``) or the store's own folder, both
keyed on the same ``tenants/{tenant_id}/...`` layout (``agents/workspace/layout.py``).

Routes:
  GET    /workspace/usage   – bytes used vs. quota for the caller's tenant
  GET    /workspace/files   – files grouped by session (thread)
  DELETE /workspace/files   – delete one file by its workspace-relative path
"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.stores import WorkspacePathError
from substrate.types.errors import ObjectNotFoundError
from substrate.workspace.layout import (
    conversation_shared_key,
    conversation_workspace_prefix,
    user_prefix,
)
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.file_records import key_owner
from substrate_cloud.monolith.file_versioning import (
    VERSIONS_DIR,
    capture_bytes,
    latest_version,
    list_versions,
    record_version,
    sha256_hex,
)
from substrate_cloud.monolith.models import Thread
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.monolith.services import get_owned_thread
from substrate_cloud.monolith.services.agents.service import (
    get_owned_agent,
    list_agents,
)
from substrate_cloud.monolith.services.groups.service import (
    get_owned_group,
    list_groups,
)
from substrate_cloud.shared.auth.claims import AuthClaims

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


def _workspace_store(
    ctx: ServerDependencies, claims: AuthClaims, owner_id: str | None = None
) -> Any:
    """The file store for *owner_id*'s files (the caller's own unless an admin is reading someone's conversation)."""
    store = ctx.files_for(claims.tenant_id, owner_id or claims.sub)
    if store is None:
        raise HTTPException(status_code=503, detail="File store not configured")
    return store


def _is_conversation_shared_key(key: str) -> bool:
    """True only for an actual conversation file under
    ``tenants/{tid}/users/{uid}/conversations/{cid}/branches/{bid}/workspace/shared/...``.
    """
    parts = key.split("/")
    return (
        len(parts) >= 11
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
        and parts[6] == "branches"
        and parts[8] == "workspace"
        and parts[9] == "shared"
    )


def _is_direct_upload_key(key: str) -> bool:
    """True for a composer/API upload with no thread — ``tenants/{tid}/
    users/{uid}/uploads/...`` (see ``routes/files.py``'s ``base_key`` when
    ``thread_id is None``) — a sibling of ``conversations/``, not nested
    under it."""
    parts = key.split("/")
    return (
        len(parts) >= 5
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "uploads"
    )


def _is_listable_workspace_key(key: str) -> bool:
    """The positive counterpart to ``_is_version_key``: ``list_files`` scans
    the caller's whole ``user_prefix``, which also contains
    ``conversations/{cid}/artifacts/...`` (curated OKF notes — deliberately
    not shown as files, see ``layout.py::conversation_artifacts_prefix``),
    the top-level ``artifacts/...`` bundle, and ``index/...`` (the search
    index, not a file at all). Only conversation-shared files and direct
    uploads belong in the Storage browser.
    """
    return _is_conversation_shared_key(key) or _is_direct_upload_key(key)


def _is_version_key(key: str) -> bool:
    """True for a snapshot under
    ``tenants/{tid}/users/{uid}/conversations/{cid}/branches/{bid}/workspace/versions/...``.

    Anchored at that fixed position rather than matching ``/versions/``
    anywhere: a conversation could otherwise have its own real ``versions``
    folder, and it must not vanish from the file list.
    """
    parts = key.split("/")
    return (
        len(parts) >= 10
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
        and parts[6] == "branches"
        and parts[8] == "workspace"
        and parts[9] == VERSIONS_DIR
    )


def _session_id_from_key(key: str) -> str | None:
    parts = key.split("/")
    # tenants/{tid}/users/{uid}/conversations/{thread_id}/branches/{bid}/...
    if (
        len(parts) >= 6
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
    ):
        return parts[5]
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
    store = _workspace_store(ctx, claims)
    # Meter quota per tenant (conversation workspaces omit user segments)
    used = await store.usage_bytes(claims.tenant_id, force=True)
    quota = ctx.workspace_user_quota_bytes
    direct = ctx.direct_files_for(claims.tenant_id, claims.sub)
    if direct is not None:
        # The volume's hard quota is the data service's; none set means the platform default is only a display figure.
        quota = (await direct.usage(claims.tenant_id)).quota_bytes or quota
    return WorkspaceUsageResponse(used_bytes=used, quota_bytes=quota)


@router.get("/files", response_model=WorkspaceFilesResponse)
async def list_files(
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> WorkspaceFilesResponse:
    store = _workspace_store(ctx, claims)
    # Non-deleted threads owned by the caller in this tenant — a soft-deleted
    # thread's files stay in storage (see thread_service.py::delete_thread)
    # but are hidden from the owner's Storage view, same as the thread
    # itself is hidden from their sidebar.
    owned_thread_ids = {
        str(tid)
        for tid in (
            await db.execute(
                select(Thread.id).where(
                    Thread.user_identifier == claims.sub,
                    Thread.tenant_id == claims.tenant_id,
                    Thread.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    }
    # The caller's agents' homes and their groups' drives are listed too, under the agent's or the group's name.
    my_agents = await list_agents(db, claims)
    my_groups = await list_groups(db, claims)
    owned_thread_ids |= {a.workspace_id for a in my_agents} | {
        g.workspace_id for g in my_groups
    }

    # One scan of the caller's whole prefix — not one call per thread plus
    # this, which used to list every conversation's shared files twice
    # (conversations now nest under users/{uid}/, so the per-thread loop's
    # results were already a subset of this single scan).
    all_entries = await store.list_prefix(
        f"{user_prefix(claims.tenant_id, claims.sub)}/"
    )

    def _keep(key: str) -> bool:
        if not _is_listable_workspace_key(key):
            # Version snapshots, curated artifact bundles, and the search
            # index are internal, not user-facing files.
            return False
        session_id = _session_id_from_key(key)
        # Direct uploads (no thread_id) have no session; a conversation
        # file's thread must be owned and not soft-deleted (files of a
        # deleted thread stay in storage but are hidden from the owner's
        # Storage view, same as the thread itself).
        return session_id is None or session_id in owned_thread_ids

    entries = [e for e in all_entries if _keep(e[0])]
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
    thread_names.update({a.workspace_id: a.name for a in my_agents})
    thread_names.update({g.workspace_id: g.name for g in my_groups})

    # A file is user-owned iff it was uploaded: uploads go to an ``uploads/`` folder (a conversation's, or the user's own), while
    # code-interpreter / assistant artifacts are written anywhere else in the session dir.
    def _uploaded(key: str) -> bool:
        if _is_direct_upload_key(key):
            return True
        marker = "/workspace/shared/"
        _, _, relative = key.partition(marker)
        return relative.startswith("uploads/")

    uploaded_keys = {key for key, _, _ in entries if _uploaded(key)}

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


async def _thread_owner(db: AsyncSession, claims: AuthClaims, thread_id: str) -> str:
    """The thread's actual owner (`Thread.user_identifier`), after
    confirming the *caller* may access it — same predicate as
    ``get_owned_thread`` (owner, or an admin: platform-wide, or
    tenant-scoped for a tenant_admin in its own tenant; a soft-deleted
    thread 404s for its owner same as elsewhere).

    Conversation storage nests under the owner's prefix, not necessarily
    the caller's — an admin reading another user's thread must still
    resolve the same key that owner's own requests do — but resolving the
    owner is only reached once the caller is confirmed authorized; a plain
    tenant match used to be enough here, letting any caller in the tenant
    read or overwrite any other caller's conversation files by thread id.
    """
    if thread_id.startswith("dot-"):
        # An agent's own workspace: only its owner may open it.
        try:
            agent = await get_owned_agent(db, uuid.UUID(thread_id[4:]), claims)
        except ValueError:
            agent = None
        if agent is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return agent.user_identifier
    if thread_id.startswith("group-"):
        # A group's drive, shared by the agents in it: only the group's owner may open it.
        try:
            group = await get_owned_group(db, uuid.UUID(thread_id[6:]), claims)
        except ValueError:
            group = None
        if group is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return group.user_identifier
    try:
        thread_uuid = uuid.UUID(thread_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread = await get_owned_thread(db, thread_uuid, claims)
    if thread is None or thread.user_identifier is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread.user_identifier


def _session_key(tenant_id: str, user_id: str, thread_id: str, path: str) -> str:
    """Build (and validate) the ownership-scoped canonical key for a
    session-relative path."""
    try:
        return conversation_shared_key(tenant_id, user_id, thread_id, path)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")


async def _resolve_session_key(
    store: Any,
    tenant_id: str,
    user_id: str,
    thread_id: str,
    path: str,
) -> str:
    """Resolve a session-relative ref to the real object key of an existing file.

    The model sometimes references a generated file by bare name
    (``report.pptx``) even though its code wrote it into a subdirectory of the
    run cwd (``out/report.pptx``). Prefer the exact path; if it doesn't exist,
    fall back to the most-recently-modified file with the same basename anywhere
    under the session dir (excluding version snapshots). When nothing matches,
    return the exact key so writes to a brand-new file still land where asked.
    """
    exact = _session_key(tenant_id, user_id, thread_id, path)
    if await store.exists(exact):
        return exact
    base = path.rsplit("/", 1)[-1]
    shared_prefix = (
        f"{conversation_workspace_prefix(tenant_id, user_id, thread_id)}/shared/"
    )
    matches = [
        (key, mtime)
        for (key, _size, mtime) in await store.list_prefix(shared_prefix)
        if not _is_version_key(key) and key.rsplit("/", 1)[-1] == base
    ]
    if matches:
        matches.sort(key=lambda item: item[1], reverse=True)
        return matches[0][0]
    return exact


def _session_rel(key: str, tenant_id: str, user_id: str, thread_id: str) -> str:
    """Inverse of `_session_key`: the session-relative path for a resolved key."""
    prefix = f"{conversation_workspace_prefix(tenant_id, user_id, thread_id)}/shared/"
    return key[len(prefix) :] if key.startswith(prefix) else key


# Non-editable formats excluded from version snapshots
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


@router.get("/file", response_model=None)
async def serve_file(
    request: Request,
    thread_id: str = Query(..., description="Conversation/thread id (session folder)"),
    path: str = Query(
        ..., description="File path relative to the thread's session dir"
    ),
    seq: int | None = Query(
        None, description="Serve a specific version (default: latest)"
    ),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse | Response:
    """Serve a code-interpreter / workspace file by its session-relative path.

    Resolves to the conversation's ``.../conversations/{thread_id}/workspace/shared/{path}`` key — the same
    per-thread directory the sandbox runs in (see the code-interpreter tools'
    ``workspace_dir``). This is what the frontend ``sandbox:<path>`` markdown
    refs resolve to: images render inline, everything else downloads.

    Serving the *current* file also lazily versions it: a change made outside
    our save endpoints (i.e. the agent rewrote it via code_interpreter) is
    captured as an ``"agent"`` version here, so the panel's version
    history stays honest without a per-turn scan.
    """
    owner_id = await _thread_owner(db, claims, thread_id)
    store = _workspace_store(ctx, claims, owner_id)
    key = await _resolve_session_key(store, claims.tenant_id, owner_id, thread_id, path)

    if seq is not None:
        version = next(
            (v for v in await list_versions(store, key) if v.seq == seq), None
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Version not found")
        source_key = version.version_key
    else:
        source_key = key

    try:
        data = await store.download(source_key)
    except (WorkspacePathError, KeyError, FileNotFoundError, ObjectNotFoundError):
        raise HTTPException(status_code=404, detail="File not found") from None

    name = key.rsplit("/", 1)[-1]
    checksum = sha256_hex(data)
    if seq is None and _is_versionable(name):
        # Capture an out-of-band (agent) change / the initial state.
        await capture_bytes(store, object_key=key, data=data)

    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    disposition = "inline" if _is_inline_type(content_type) else "attachment"

    # seq-pinned versions are immutable (public cache); latest versions use ETag revalidation
    etag = f'"{checksum}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": "public, max-age=31536000, immutable"
                if seq is not None
                else "private, no-cache",
            },
        )

    async def _stream():
        yield data

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{name}"',
            "X-File-Checksum": checksum,
            "ETag": etag,
            "Cache-Control": "public, max-age=31536000, immutable"
            if seq is not None
            else "private, no-cache",
        },
    )


@router.put("/file", status_code=200)
async def save_file(
    request: Request,
    thread_id: str = Query(...),
    path: str = Query(...),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    """Save edited bytes back to a workspace file — used by text/Monaco editors
    and, client-side, the BetterOffice Office editors.

    Optimistic concurrency: the client sends the checksum it loaded in
    ``X-Base-Checksum``; if the canonical file changed since (the agent wrote
    it), respond 409 so the UI can offer reload-vs-overwrite. The prior state
    is already versioned (via serve_file's lazy capture), so nothing is lost.
    """
    owner_id = await _thread_owner(db, claims, thread_id)
    store = _workspace_store(ctx, claims, owner_id)
    key = await _resolve_session_key(store, claims.tenant_id, owner_id, thread_id, path)
    body = await request.body()
    base = request.headers.get("X-Base-Checksum", "")

    current: bytes = b""
    current_checksum = ""
    try:
        current = await store.download(key)
        current_checksum = sha256_hex(current)
    except (WorkspacePathError, KeyError, FileNotFoundError, ObjectNotFoundError):
        pass

    if base and current_checksum and base != current_checksum:
        latest = await latest_version(store, key)
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
        await capture_bytes(store, object_key=key, data=current)
    await store.upload(key, body)
    version = await record_version(store, object_key=key, data=body, author="user")
    return {"checksum": version.checksum_sha256, "seq": version.seq}


@router.get("/versions", response_model=WorkspaceVersionsResponse)
async def get_versions(
    thread_id: str = Query(...),
    path: str = Query(...),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> WorkspaceVersionsResponse:
    owner_id = await _thread_owner(db, claims, thread_id)
    store = _workspace_store(ctx, claims, owner_id)
    key = await _resolve_session_key(store, claims.tenant_id, owner_id, thread_id, path)
    versions = await list_versions(store, key)
    return WorkspaceVersionsResponse(
        versions=[
            WorkspaceVersionEntry(
                seq=v.seq,
                author=v.author,
                checksum_sha256=v.checksum_sha256,
                size_bytes=v.size_bytes,
                created_at=v.created_at,
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
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict:
    """Restore a prior version: copy that snapshot's bytes to the canonical
    file as a new ``"restore"`` version tagged with the seq it came from
    (non-destructive — the current state was already captured, so it stays in
    history too)."""
    owner_id = await _thread_owner(db, claims, body.thread_id)
    store = _workspace_store(ctx, claims, owner_id)
    key = await _resolve_session_key(
        store, claims.tenant_id, owner_id, body.thread_id, body.path
    )
    version = next(
        (v for v in await list_versions(store, key) if v.seq == body.seq), None
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    try:
        data = await store.download(version.version_key)
    except (WorkspacePathError, KeyError, FileNotFoundError, ObjectNotFoundError):
        raise HTTPException(status_code=404, detail="Snapshot missing") from None

    # Capture the current state first (so restoring doesn't lose it), then write.
    try:
        current = await store.download(key)
        await capture_bytes(store, object_key=key, data=current)
    except (WorkspacePathError, KeyError, FileNotFoundError, ObjectNotFoundError):
        pass
    await store.upload(key, data)
    new_version = await record_version(
        store,
        object_key=key,
        data=data,
        author="restore",
        restored_from_seq=body.seq,
    )
    return {"checksum": new_version.checksum_sha256, "seq": new_version.seq}


@router.delete("/files", status_code=204)
async def delete_file(
    path: str = Query(..., description="Workspace-relative file path"),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> None:
    if not ctx.workspace_user_delete_allowed:
        raise HTTPException(
            status_code=403, detail="Storage deletion is disabled for this deployment."
        )
    # A single-user (personal/dev) tenant has nobody else's data to protect,
    # so any owner may delete their own files there. Once a second
    # *currently-registered* user is present, deletion becomes admin-only —
    # one person's cleanup shouldn't be able to remove files another user's
    # conversation still depends on. Soft-deleted threads' owners don't
    # count: a user whose only threads are gone isn't "present" anymore.
    other_users = (
        await db.execute(
            select(func.count(func.distinct(Thread.user_identifier))).where(
                Thread.tenant_id == claims.tenant_id,
                Thread.user_identifier.is_not(None),
                Thread.user_identifier != claims.sub,
                Thread.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    if other_users > 0 and not claims.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only an admin may delete files once more than one user is present.",
        )

    # thread_uuid is resolved unconditionally (not only when the path falls
    # outside the caller's own prefix) — it's needed below to lock the
    # conversation on delete regardless of who's deleting, and under the
    # nested layout a caller's *own* conversation files always start with
    # their own prefix, so the old "only resolve it in the other branch"
    # left the lock permanently unreachable for the common case (deleting
    # your own file).
    own_prefix = f"{user_prefix(claims.tenant_id, claims.sub)}/"
    session_id = _session_id_from_key(path)
    thread_uuid: uuid.UUID | None = None
    if session_id is not None:
        try:
            thread_uuid = uuid.UUID(session_id)
        except ValueError:
            thread_uuid = None

    if not path.startswith(own_prefix):
        # Not the caller's own prefix — only reachable for an admin (the
        # other_users check above already 403s a non-admin here). If this
        # file belongs to a conversation, that conversation must actually
        # resolve for this caller (get_owned_thread's admin bypass — same
        # predicate as everywhere else, so a tenant_admin can reach any
        # thread in its own tenant, a platform_admin any thread at all —
        # rather than re-checking the *caller's own* sub against the
        # thread's owner, which always 404d for an admin deleting someone
        # else's file).
        if not claims.is_admin:
            raise HTTPException(status_code=404, detail="File not found")
        if (
            thread_uuid is not None
            and await get_owned_thread(db, thread_uuid, claims) is None
        ):
            raise HTTPException(status_code=404, detail="File not found")

    # The file is deleted as its owner: the data service lets no one else's token touch a user's folder.
    store = _workspace_store(ctx, claims, key_owner(path))
    try:
        await store.delete(path)
    except WorkspacePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Deleting a file the conversation may still reference (earlier turns,
    # agent context) makes further replies in that thread unreliable — lock
    # it read-only rather than let the agent silently act on a file that no
    # longer exists. A real column (see models.py's Thread.locked_at), not
    # `metadata` — PATCH /threads only ever touches `metadata`, so a user
    # could otherwise clear their own lock by editing the thread's name.
    if thread_uuid is not None:
        thread = await db.get(Thread, thread_uuid)
        if thread is not None:
            thread.locked_at = datetime.now(timezone.utc)
            thread.locked_reason = (
                f"A file was deleted from this conversation's storage: "
                f"{path.rsplit('/', 1)[-1]}"
            )

    await db.commit()
