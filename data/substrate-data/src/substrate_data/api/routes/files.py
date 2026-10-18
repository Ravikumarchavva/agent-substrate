"""/api/v1/volumes/{volume}/…: the Files API, and /api/v1/upload-sessions.

`{volume}` is the volume's three-part name, `catalog.schema.volume`. File bytes never pass through
these routes: clients send and fetch them with the presigned URLs returned here. Reads need
READ_VOLUME and mutations WRITE_VOLUME (existing volume privileges); delegated tokens additionally need
the `files:read` / `files:write` scope, and paths under `users/{sub}/` belong to that subject.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from substrate_data.api.routes.assets import visible
from substrate_data.api.routes.catalog import if_match as parse_if_match
from substrate_data.api.routes.catalog import owns
from substrate_data.api.services import (
    Services,
    decode_token,
    encode_token,
    page_size,
    principal,
    services,
)
from substrate_data.auth.principal import Principal
from substrate_data.auth.scopes import require_scope
from substrate_data.core.errors import Problem, not_found
from substrate_data.files.paths import (  # noqa: F401
    like_pattern,
    normalize_path,
    normalize_prefix,
    overlaps,
)
from substrate_data.files.service import (
    MAX_SIGN_ITEMS,
    FilesService,
    path_hash,
    public_file,
    public_version,
)

router = APIRouter(prefix="/api/v1", tags=["files"])
Caller = Annotated[Principal, Depends(principal)]
Svc = Annotated[Services, Depends(services)]
READ = require_scope("files:read", "files:write")
WRITE = require_scope("files:write")
SHA256 = Field(default=None, pattern=r"^[0-9a-f]{64}$")
_lock = threading.Lock()


def files_service_for(app: FastAPI) -> FilesService:
    """The Files service, built on first use (tests and embedders may set `app.state.files`)."""
    service = getattr(app.state, "files", None)
    if service is None:
        with _lock:
            service = getattr(app.state, "files", None)
            if service is None:
                svc: Services = app.state.services
                service = app.state.files = FilesService(svc.settings, svc.store)
    return service


def files_service(request: Request) -> FilesService:
    return files_service_for(request.app)


Files = Annotated[FilesService, Depends(files_service)]


# ------------------------------------------------------------------ request bodies
class InitiateUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0)
    content_type: str | None = Field(default=None, max_length=255)
    sha256: str | None = SHA256


class PartETag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=1, max_length=200)


class CompleteUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(min_length=1, max_length=64)
    parts: list[PartETag] = Field(default_factory=list, max_length=10000)


class SignItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    action: Literal["get", "put"]
    size: int | None = Field(default=None, ge=0)
    content_type: str | None = Field(default=None, max_length=255)
    sha256: str | None = SHA256


class SignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SignItem] = Field(min_length=1, max_length=MAX_SIGN_ITEMS)


class DeletePrefix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = Field(min_length=1, max_length=1024)


class CopyPrefix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_prefix: str = Field(min_length=1, max_length=1024)
    dest_prefix: str = Field(min_length=1, max_length=1024)
    overwrite: bool = True


class Restore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(default=None, ge=1)


class QuotaSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_bytes: int | None = Field(ge=0)


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: int = Field(ge=0)
    content_type: str | None = Field(default=None, max_length=255)
    sha256: str | None = SHA256


class Promote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    volume: str = Field(pattern=r"^[^.]+\.[^.]+\.[^.]+$")
    path: str = Field(min_length=1, max_length=1024)
    parts: list[PartETag] = Field(default_factory=list, max_length=10000)


# ------------------------------------------------------------------ helpers
def volume_for(svc: Services, caller: Principal, name: str, privilege: str) -> dict[str, Any]:
    parts = name.split(".")
    if len(parts) != 3:
        raise Problem(400, "invalid-request", "A volume is named catalog.schema.volume")
    record = visible(svc, caller, "volume", *parts)
    svc.store.require(caller, "volume", record, privilege)
    return record


def full_name(volume: dict[str, Any]) -> str:
    return f"{volume['catalog_name']}.{volume['schema_name']}.{volume['name']}"


def audit(svc: Services, request: Request, caller: Principal, action: str, volume: dict, **detail):
    """Ids and counts only: a file's name is recorded as a hash, never as itself."""
    svc.store.audit(
        caller,
        action,
        full_name(volume),
        "success",
        request.state.request_id,
        volume_id=volume["id"],
        **detail,
    )


def preconditions(if_match: str | None, if_none_match: str | None) -> tuple[int | None, bool]:
    if if_none_match is not None and if_none_match.strip() not in {"*"}:
        raise Problem(400, "invalid-request", "If-None-Match supports only *")
    if if_match is not None and if_match.strip() == "*":
        return None, False
    return parse_if_match(if_match), if_none_match is not None


def etag(response: Response, file: dict[str, Any]) -> None:
    response.headers["ETag"] = f'"{file["version"]}"'


IfMatch = Annotated[str | None, Header(alias="If-Match")]
IfNoneMatch = Annotated[str | None, Header(alias="If-None-Match")]


# ------------------------------------------------------------------ uploads
@router.post("/volumes/{volume}/files:initiate-upload", dependencies=[WRITE])
async def initiate_upload(
    volume: str,
    body: InitiateUpload,
    svc: Svc,
    files: Files,
    caller: Caller,
    match: IfMatch = None,
    none_match: IfNoneMatch = None,
):
    record = volume_for(svc, caller, volume, "WRITE_VOLUME")
    path = normalize_path(body.path)
    files.policy.authorize(caller, path, "write")
    expected, create_only = preconditions(match, none_match)
    return await run_in_threadpool(
        files.initiate_upload,
        record,
        caller,
        path=path,
        size=body.size,
        content_type=body.content_type,
        sha256=body.sha256 or "",
        if_match=expected,
        if_none_match=create_only,
    )


@router.post("/volumes/{volume}/files:complete-upload", dependencies=[WRITE])
async def complete_upload(
    volume: str,
    body: CompleteUpload,
    svc: Svc,
    files: Files,
    caller: Caller,
    request: Request,
    response: Response,
    match: IfMatch = None,
    none_match: IfNoneMatch = None,
):
    record = volume_for(svc, caller, volume, "WRITE_VOLUME")
    expected, create_only = preconditions(match, none_match)
    head = await run_in_threadpool(
        files.complete_upload,
        record,
        caller,
        body.upload_id,
        [p.model_dump() for p in body.parts],
        if_match=expected,
        if_none_match=create_only,
    )
    audit(
        svc, request, caller, "file.upload", record,
        file_id=head["id"], version=head["version"], size=head["size"], path_hash=path_hash(head["path"]),
    )  # fmt: skip
    etag(response, head)
    return public_file(head)


@router.post("/volumes/{volume}/files:sign", dependencies=[READ])
async def sign(
    volume: str, body: SignRequest, svc: Svc, files: Files, caller: Caller, request: Request
):
    """Batch presigned URLs: `get` returns a download URL, `put` an upload to finish with
    `files:complete-upload`. Each item succeeds or carries its own `error`."""
    wants_put = any(item.action == "put" for item in body.items)
    record = volume_for(svc, caller, volume, "WRITE_VOLUME" if wants_put else "READ_VOLUME")
    if wants_put:
        if caller.scopes is not None and "files:write" not in caller.scopes:
            raise Problem(
                403, "insufficient-scope", "Signing uploads needs the files:write scope",
                required=["files:write"],
            )  # fmt: skip
        if any(i.action == "get" for i in body.items):
            svc.store.require(caller, "volume", record, "READ_VOLUME")
    results = await run_in_threadpool(
        files.sign, record, caller, [item.model_dump() for item in body.items]
    )
    audit(
        svc, request, caller, "file.sign", record,
        items=len(results), puts=sum(1 for i in body.items if i.action == "put"),
        errors=sum(1 for r in results if "error" in r),
        path_hashes=[path_hash(i.path) for i in body.items],
    )  # fmt: skip
    return {"items": results}


# ------------------------------------------------------------------ listing and metadata
@router.get("/volumes/{volume}/files", dependencies=[READ])
def list_files(
    volume: str,
    svc: Svc,
    files: Files,
    caller: Caller,
    prefix: str = Query("", max_length=1024),
    delimiter: str | None = Query(None, pattern=r"^/$"),
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    record = volume_for(svc, caller, volume, "READ_VOLUME")
    clean = normalize_prefix(prefix)
    visible_entry = files.policy.authorize_prefix(caller, clean, "read")
    found, directories, token = files.catalog.list_entries(
        record["id"],
        clean,
        delimiter,
        decode_token(page_token),
        page_size(page_size_),
        visible_entry,
    )
    return {
        "files": [public_file(row) for row in found],
        "directories": directories,
        "next_page_token": encode_token(token) if token else None,
    }


@router.get("/volumes/{volume}/files/{path:path}/versions", dependencies=[READ])
def list_versions(
    volume: str,
    path: str,
    svc: Svc,
    files: Files,
    caller: Caller,
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    record = volume_for(svc, caller, volume, "READ_VOLUME")
    clean = normalize_path(path)
    files.policy.authorize(caller, clean, "read")
    limit = page_size(page_size_)
    before = decode_token(page_token)
    rows = files.catalog.versions(record["id"], clean, int(before) if before else None, limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "versions": [public_version(r) for r in rows],
        "next_page_token": encode_token(str(rows[-1]["version"])) if more else None,
    }


@router.get("/volumes/{volume}/files/{path:path}:download-url", dependencies=[READ])
def download_url(
    volume: str,
    path: str,
    svc: Svc,
    files: Files,
    caller: Caller,
    request: Request,
    version: int | None = Query(None, ge=1),
    expires_in: int | None = Query(None, ge=30, le=7 * 24 * 3600),
):
    record = volume_for(svc, caller, volume, "READ_VOLUME")
    clean = normalize_path(path)
    files.policy.authorize(caller, clean, "read")
    result = files.download_url(record, clean, version, expires_in)
    audit(
        svc,
        request,
        caller,
        "file.download_url",
        record,
        path_hash=path_hash(clean),
        version=result["version"],
    )
    return result


@router.get("/volumes/{volume}/files/{path:path}", dependencies=[READ])
def file_metadata(
    volume: str, path: str, svc: Svc, files: Files, caller: Caller, response: Response
):
    record = volume_for(svc, caller, volume, "READ_VOLUME")
    clean = normalize_path(path)
    files.policy.authorize(caller, clean, "read")
    head = files.catalog.head(record["id"], clean)
    if head is None:
        raise not_found("File")
    etag(response, head)
    return public_file(head)


# ------------------------------------------------------------------ changes
@router.delete("/volumes/{volume}/files/{path:path}", status_code=204, dependencies=[WRITE])
def delete_file(
    volume: str,
    path: str,
    svc: Svc,
    files: Files,
    caller: Caller,
    request: Request,
    match: IfMatch = None,
):
    """Soft delete: the file disappears from listings; its versions stay until purged."""
    record = volume_for(svc, caller, volume, "WRITE_VOLUME")
    clean = normalize_path(path)
    files.policy.authorize(caller, clean, "write")
    expected, _ = preconditions(match, None)
    removed = files.catalog.soft_delete(record["id"], clean, if_match=expected)
    if removed is None:
        raise not_found("File")
    audit(
        svc,
        request,
        caller,
        "file.delete",
        record,
        file_id=removed["id"],
        path_hash=path_hash(clean),
    )


@router.post("/volumes/{volume}/files:delete-prefix", dependencies=[WRITE])
def delete_prefix(
    volume: str, body: DeletePrefix, svc: Svc, files: Files, caller: Caller, request: Request
):
    record = volume_for(svc, caller, volume, "WRITE_VOLUME")
    prefix = normalize_prefix(body.prefix)
    if not prefix:
        raise Problem(400, "invalid-request", "A prefix is required; delete the volume instead")
    files.policy.authorize_prefix(caller, prefix, "write")
    count, size = files.catalog.delete_prefix(record["id"], prefix)
    audit(
        svc,
        request,
        caller,
        "file.delete_prefix",
        record,
        prefix_hash=path_hash(prefix),
        files=count,
        bytes=size,
    )
    return {"deleted": count, "bytes": size}


@router.post("/volumes/{volume}/files:copy-prefix", dependencies=[WRITE])
def copy_prefix(
    volume: str, body: CopyPrefix, svc: Svc, files: Files, caller: Caller, request: Request
):
    """Copy a folder inside the volume (a fork or a promotion). The stored objects are shared, so it
    is instant and costs no storage; the copies are independent files afterwards."""
    record = volume_for(svc, caller, volume, "WRITE_VOLUME")
    source, destination = normalize_prefix(body.source_prefix), normalize_prefix(body.dest_prefix)
    if not source or not destination:
        raise Problem(400, "invalid-request", "Source and destination prefixes are required")
    if overlaps(source, destination):
        raise Problem(400, "invalid-request", "The prefixes must not contain one another")
    files.policy.authorize_prefix(caller, source, "read")
    files.policy.authorize_prefix(caller, destination, "write")
    result = files.catalog.copy_prefix(
        record["id"], source, destination, caller.id, overwrite=body.overwrite
    )
    audit(
        svc, request, caller, "file.copy_prefix", record,
        source_hash=path_hash(source), dest_hash=path_hash(destination), **result,
    )  # fmt: skip
    return result


@router.post("/volumes/{volume}/files/{path:path}:restore", dependencies=[WRITE])
def restore_file(
    volume: str,
    path: str,
    body: Restore,
    svc: Svc,
    files: Files,
    caller: Caller,
    request: Request,
    response: Response,
    match: IfMatch = None,
):
    """Undelete a file, or (with `version`) make an older version current again as a new version."""
    record = volume_for(svc, caller, volume, "WRITE_VOLUME")
    clean = normalize_path(path)
    files.policy.authorize(caller, clean, "write")
    expected, _ = preconditions(match, None)
    head = files.catalog.restore(record["id"], clean, body.version, caller.id, if_match=expected)
    audit(
        svc,
        request,
        caller,
        "file.restore",
        record,
        file_id=head["id"],
        version=head["version"],
        path_hash=path_hash(clean),
    )
    etag(response, head)
    return public_file(head)


# ------------------------------------------------------------------ usage and quotas
@router.get("/volumes/{volume}/usage", dependencies=[READ])
def usage(
    volume: str,
    svc: Svc,
    files: Files,
    caller: Caller,
    prefix: str = Query("", max_length=1024),
):
    record = volume_for(svc, caller, volume, "READ_VOLUME")
    clean = normalize_prefix(prefix)
    files.policy.authorize_prefix(caller, clean, "read")
    return {
        "volume": full_name(record),
        "prefix": clean,
        **files.catalog.usage(record["id"], clean),
    }


@router.put("/volumes/{volume}/quotas")
def set_quota(
    volume: str, body: QuotaSet, svc: Svc, files: Files, caller: Caller, request: Request
):
    """Set (or with `null` remove) the volume's hard byte quota. Owner or admin only."""
    record = volume_for(svc, caller, volume, "READ_VOLUME")
    owns(svc, caller, "volume", record)
    files.catalog.set_quota(record["id"], body.max_bytes, caller.id)
    audit(svc, request, caller, "volume.quota", record, max_bytes=body.max_bytes)
    return {"volume": full_name(record), "quota_bytes": body.max_bytes}


# ------------------------------------------------------------------ upload sessions
@router.post("/upload-sessions", status_code=201, dependencies=[WRITE])
async def create_session(
    body: SessionCreate, svc: Svc, files: Files, caller: Caller, request: Request
):
    """A pending upload not yet in any volume: send the bytes, then promote it into a volume path."""
    started = await run_in_threadpool(
        files.create_session,
        caller,
        size=body.size,
        content_type=body.content_type,
        sha256=body.sha256 or "",
    )
    svc.store.audit(
        caller, "upload_session.create", None, "success", request.state.request_id,
        session_id=started["id"], size=body.size,
    )  # fmt: skip
    return started


@router.post("/upload-sessions/{session_id}:promote", dependencies=[WRITE])
async def promote_session(
    session_id: str,
    body: Promote,
    svc: Svc,
    files: Files,
    caller: Caller,
    request: Request,
    response: Response,
    match: IfMatch = None,
    none_match: IfNoneMatch = None,
):
    record = volume_for(svc, caller, body.volume, "WRITE_VOLUME")
    path = normalize_path(body.path)
    expected, create_only = preconditions(match, none_match)
    head = await run_in_threadpool(
        files.promote,
        session_id,
        record,
        caller,
        path=path,
        parts=[p.model_dump() for p in body.parts],
        if_match=expected,
        if_none_match=create_only,
    )
    audit(
        svc, request, caller, "upload_session.promote", record,
        session_id=session_id, file_id=head["id"], version=head["version"], path_hash=path_hash(path),
    )  # fmt: skip
    etag(response, head)
    return public_file(head)
