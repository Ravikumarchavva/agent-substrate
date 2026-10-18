"""/api/v1/erasure-requests and /api/v1/exports: one person's (or one tenant's) data, removed or handed over.

Both cover every domain this service holds: files and their versions, unfinished uploads, document libraries (catalog,
vectors and bundles) and long-term memory. They declare no scope, so a delegated (agent) token can never use them. Who may
ask: the subject itself, a tenant administrator, or a platform administrator. The audit log is part of the evidence, not of
the data: it keeps the digests and counts it always held and its hash chain still verifies afterwards.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from substrate.stores.memory import MemoryNamespace, MemoryQuery, MemoryStatus, TenantWide

from substrate_data.api.routes.catalog import Caller, Svc
from substrate_data.api.routes.documents import Documents, Domain, wire
from substrate_data.auth.principal import Principal
from substrate_data.core.errors import Problem, forbidden
from substrate_data.documents.volume import tenant_volume
from substrate_data.files.policy import TENANT_ADMIN_GROUP

router = APIRouter(prefix="/api/v1", tags=["privacy"])
EXPORT_LIMIT = 10_000
EXPORT_URL_TTL = 3600
Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}$")]


class Target(BaseModel):
    """Whose data: one subject of a tenant, or (`scope: tenant`) all of it."""

    model_config = ConfigDict(extra="forbid")
    scope: Literal["user", "tenant"]
    subject: Id | None = None
    tenant_id: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")] = None


def authorize(caller: Principal, body: Target) -> tuple[str, str | None]:
    tenant = body.tenant_id or caller.tenant_id
    if not tenant:
        raise Problem(400, "invalid-request", "Name the tenant (tenant_id)")
    if body.scope == "user" and not body.subject:
        raise Problem(400, "invalid-request", "scope user needs a subject")
    if body.scope == "tenant" and body.subject:
        raise Problem(400, "invalid-request", "scope tenant takes no subject")
    tenant_admin = TENANT_ADMIN_GROUP in caller.groups and caller.tenant_id == tenant
    itself = body.scope == "user" and caller.tenant_id == tenant and caller.subject == body.subject
    if not (caller.is_admin or tenant_admin or itself):
        raise forbidden("Only the subject, a tenant administrator or a platform administrator may do this")
    return tenant, body.subject if body.scope == "user" else None


def prefixes(tenant: str, subject: str | None) -> tuple[str, str]:
    """`(path prefix inside the volume, collection-name prefix)`."""
    if subject is None:
        return "", f"tenants/{tenant}"
    return f"users/{subject}/", f"tenants/{tenant}/users/{subject}"


def subject_hash(subject: str | None) -> str | None:
    return hashlib.sha256(subject.encode()).hexdigest()[:16] if subject else None


async def provisioned_volume(domain: Documents, tenant: str) -> dict[str, Any] | None:
    try:
        return await asyncio.to_thread(tenant_volume, domain.files, tenant)
    except Problem as exc:
        if exc.code == "tenant-not-provisioned":
            return None
        raise


# ------------------------------------------------------------------ erasure
@router.post("/erasure-requests", status_code=201)
async def erase(body: Target, svc: Svc, caller: Caller, domain: Domain, request: Request):
    tenant, subject = authorize(caller, body)
    files = domain.files
    engine = domain.engine
    path_prefix, name_prefix = prefixes(tenant, subject)
    steps: dict[str, Any] = {}

    # 1. libraries: the vectors first (so they can be counted), then the catalog and the bundles' index files
    store = engine.store
    steps["vectors"] = await store.vectors.erase_under(name_prefix) if await store.has("vectors") else 0
    registry = await engine.collections_of(tenant, prefix=name_prefix)
    documents = 0
    for row in registry:
        documents += await engine.library(row).erase_under(row.name)
    if await store.has("library"):
        documents += await engine.lexical.erase_under(name_prefix)  # collections never registered (written by an older client)
    await engine.forget_collections([r.id for r in registry])
    steps["libraries"] = {"collections": len(registry), "documents": documents}

    # 2. memory
    namespace = MemoryNamespace(tenant_id=tenant, user_id=subject)
    steps["memory"] = await store.memory.erase(namespace) if await store.has("memory") else 0

    # 3. files with every version, then the objects nothing references any more; and unfinished uploads
    volume = await provisioned_volume(domain, tenant)
    result = {"files": 0, "versions": 0, "objects": 0}
    uploads = 0
    if volume is not None:
        erased = await asyncio.to_thread(files.catalog.erase_prefix, volume["id"], path_prefix)
        garbage = erased.pop("garbage")
        if garbage:
            await asyncio.to_thread(files.storage.delete, garbage)
        result = erased
        pending = []
        if subject is None:
            pending = await asyncio.to_thread(files.catalog.erase_uploads, volume_id=volume["id"])
        else:
            for principal_id in {f"user:{subject}", *([caller.id] if caller.subject == subject else [])}:
                pending += await asyncio.to_thread(files.catalog.erase_uploads, created_by=principal_id)
        for upload in pending:
            if upload["upload_id"]:
                await asyncio.to_thread(files.storage.abort_multipart, upload["object_key"], upload["upload_id"])
            await asyncio.to_thread(files.storage.delete, [upload["object_key"]])
        uploads = len(pending)
    steps["files"] = result
    steps["upload_sessions"] = uploads

    # 4. the freed space: deleted rows and index entries leave the database files
    await store.database.reclaim()
    receipt = {
        "id": uuid4().hex,
        "status": "complete",
        "scope": body.scope,
        "tenant_id": tenant,
        "subject_hash": subject_hash(subject),
        "completed_at": time.time(),
        "removed": steps,
        "retained": {"audit_log": "ids and digests only; the hash chain is kept"},
    }
    svc.store.audit(
        caller, "erasure.complete", f"tenant:{tenant}", "success", request.state.request_id,
        erasure_id=receipt["id"], scope=body.scope, subject_hash=receipt["subject_hash"], removed=steps,
    )  # fmt: skip
    return receipt


# ------------------------------------------------------------------ export
@router.post("/exports", status_code=201)
async def export(body: Target, svc: Svc, caller: Caller, domain: Domain, request: Request):
    tenant, subject = authorize(caller, body)
    files = domain.files
    engine = domain.engine
    path_prefix, name_prefix = prefixes(tenant, subject)
    truncated = False

    listed: list[dict[str, Any]] = []
    volume = await provisioned_volume(domain, tenant)
    if volume is not None:

        def collect() -> list[dict[str, Any]]:
            found: list[dict[str, Any]] = []
            after = ""
            while len(found) < EXPORT_LIMIT:
                rows, _dirs, after = files.catalog.list_entries(
                    volume["id"], path_prefix, None, after, 500, lambda _p: True
                )
                for row in rows:
                    obj = files.catalog.current_object(volume["id"], row["path"])
                    found.append(
                        {
                            "path": row["path"],
                            "size": int(row["size"]),
                            "sha256": row["sha256"] or None,
                            "content_type": row["content_type"],
                            "version": int(row["version"]),
                            "url": files.storage.sign_get(
                                obj["object_key"], expires_in=EXPORT_URL_TTL
                            ),
                        }
                    )
                if not after:
                    break
            return found

        listed = await asyncio.to_thread(collect)
        truncated = len(listed) >= EXPORT_LIMIT

    collections = []
    for row in await engine.collections_of(tenant, prefix=name_prefix):
        library, docs, after = engine.library(row), [], None
        while True:
            page = await library.list(collection=row.name, limit=200, after=after)
            docs += [wire(d) for d in page.documents]
            after = page.next
            if after is None:
                break
        collections.append({"id": row.id, "name": row.name, "documents": docs})

    records: list[dict[str, Any]] = []
    if await engine.store.has("memory"):
        matches = await engine.store.memory.query(
            MemoryQuery(
                namespace=MemoryNamespace(tenant_id=tenant, user_id=subject),
                tenant_wide=TenantWide(reason="data export"),
                statuses=list(MemoryStatus),
                limit=EXPORT_LIMIT,
            )
        )
        records = [
            m.record.model_dump(mode="json")
            for m in matches
            if subject is None or m.record.namespace.user_id == subject
        ]
        truncated = truncated or len(matches) >= EXPORT_LIMIT

    export_id = uuid4().hex
    svc.store.audit(
        caller, "export.create", f"tenant:{tenant}", "success", request.state.request_id,
        export_id=export_id, scope=body.scope, subject_hash=subject_hash(subject),
        files=len(listed), collections=len(collections), memory=len(records),
    )  # fmt: skip
    return {
        "id": export_id,
        "format": "substrate-export/v1",
        "generated_at": time.time(),
        "scope": body.scope,
        "tenant_id": tenant,
        "subject": subject,
        "truncated": truncated,
        "url_expires_in": EXPORT_URL_TTL,
        "files": listed,
        "collections": collections,
        "memory": records,
    }
