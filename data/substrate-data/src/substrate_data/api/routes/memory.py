"""/api/v1/memory: long-term memory records (docs/DOCUMENTS.md).

The `Memory` engine of the agent library behind substrate-data's authorization. The tenant always comes from the verified
token, never from the request. A record that names a user belongs to that user (a delegated token may name only its own
subject); a record with no user is a tenant-level fact, which only a tenant administrator may write or delete. Delegated
tokens need `memory:read` / `memory:write`. The audit log records record ids' digests, never their content.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from substrate.stores.memory import (
    MemoryCategory,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
    TenantWide,
)
from substrate.types.errors import ScopeViolationError

from substrate_data.api.routes.catalog import Caller, Svc
from substrate_data.api.routes.documents import Domain, digest
from substrate_data.auth.principal import Principal
from substrate_data.auth.scopes import require_scope
from substrate_data.core.errors import Problem, forbidden, not_found
from substrate_data.files.policy import TENANT_ADMIN_GROUP

router = APIRouter(prefix="/api/v1", tags=["memory"])
READ = require_scope("memory:read", "memory:write")
WRITE = require_scope("memory:write")
MAX_QUERY = 100
OwnerId = Annotated[str | None, Query(min_length=1, max_length=200)]


class NamespaceIn(BaseModel):
    """Who is asking or who owns: the tenant is the token's and is not part of the body."""

    model_config = ConfigDict(extra="forbid")
    user_id: str | None = Field(default=None, min_length=1, max_length=200)
    agent_id: str | None = Field(default=None, min_length=1, max_length=200)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: NamespaceIn = Field(default_factory=NamespaceIn)
    tenant_wide_reason: str | None = Field(default=None, min_length=3, max_length=500)
    text_query: str | None = Field(default=None, max_length=2000)
    embedding: list[float] | None = Field(default=None, max_length=8192)
    categories: list[MemoryCategory] | None = None
    statuses: list[MemoryStatus] = Field(default_factory=lambda: [MemoryStatus.ACTIVE])
    limit: int = Field(default=10, ge=1, le=MAX_QUERY)
    min_score: float = 0.0
    metadata_filter: dict[str, Any] | None = None


class TouchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: NamespaceIn = Field(default_factory=NamespaceIn)
    ids: list[str] = Field(min_length=1, max_length=500)


def privileged(caller: Principal) -> bool:
    return caller.is_admin or TENANT_ADMIN_GROUP in caller.groups


def namespace_of(caller: Principal, owner: NamespaceIn, *, write: bool = False) -> MemoryNamespace:
    """The caller's namespace: its verified tenant, and only a user it is allowed to speak as."""
    if not caller.tenant_id:
        raise Problem(403, "tenant-required", "This token names no tenant, so it has no memory")
    if owner.user_id is not None and owner.user_id != caller.subject and not privileged(caller):
        raise forbidden("This token may not act for another user's memory")
    if write and owner.user_id is None and not privileged(caller):
        raise forbidden("Tenant-level memory is written by a tenant administrator")
    return MemoryNamespace(
        tenant_id=caller.tenant_id,
        user_id=owner.user_id,
        agent_id=owner.agent_id,
        session_id=owner.session_id,
    )


def public(record: MemoryRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def audit(svc, request: Request, caller: Principal, action: str, record_id: str, **detail: Any):
    svc.store.audit(
        caller, action, "memory", "success", request.state.request_id,
        record_hash=digest(record_id), **detail,
    )  # fmt: skip


@router.put("/memory/records/{record_id}", dependencies=[WRITE])
async def save(
    record_id: str, body: dict[str, Any], svc: Svc, caller: Caller, domain: Domain, request: Request
):
    owner = NamespaceIn.model_validate(
        {k: v for k, v in (body.get("namespace") or {}).items() if k != "tenant_id"}
    )
    namespace = namespace_of(caller, owner, write=True)
    try:
        record = MemoryRecord.model_validate(
            {**body, "id": record_id, "namespace": namespace.model_dump()}
        )
    except ValueError as exc:
        raise Problem(400, "invalid-request", f"Not a memory record: {str(exc)[:300]}") from None
    try:
        await domain.engine.store.memory.save(record)
    except ScopeViolationError:
        raise Problem(
            403, "scope-violation", "That record id belongs to another owner in this tenant"
        ) from None
    audit(svc, request, caller, "memory.save", record_id, category=record.category.value)
    return {"id": record_id}


@router.get("/memory/records/{record_id}", dependencies=[READ])
async def get(
    record_id: str, caller: Caller, domain: Domain,
    user_id: OwnerId = None, agent_id: OwnerId = None, session_id: OwnerId = None,
):  # fmt: skip
    namespace = namespace_of(caller, NamespaceIn(user_id=user_id, agent_id=agent_id, session_id=session_id))
    record = await domain.engine.store.memory.get(namespace, record_id)
    if record is None:
        raise not_found("Memory record")
    return public(record)


@router.delete("/memory/records/{record_id}", dependencies=[WRITE])
async def delete(
    record_id: str, svc: Svc, caller: Caller, domain: Domain, request: Request,
    user_id: OwnerId = None, agent_id: OwnerId = None, session_id: OwnerId = None,
):  # fmt: skip
    namespace = namespace_of(
        caller, NamespaceIn(user_id=user_id, agent_id=agent_id, session_id=session_id), write=True
    )
    if not await domain.engine.store.memory.delete(namespace, record_id):
        raise not_found("Memory record")
    audit(svc, request, caller, "memory.delete", record_id)
    return Response(status_code=204)


@router.post("/memory/records:query", dependencies=[READ])
async def query(body: QueryIn, caller: Caller, domain: Domain):
    namespace = namespace_of(caller, body.namespace)
    wide = None
    if body.tenant_wide_reason is not None:
        if not privileged(caller):
            raise forbidden("Reading across a tenant's users needs a tenant administrator")
        wide = TenantWide(reason=body.tenant_wide_reason)
    matches = await domain.engine.store.memory.query(
        MemoryQuery(
            namespace=namespace,
            tenant_wide=wide,
            text_query=body.text_query,
            embedding=body.embedding,
            categories=body.categories,
            statuses=body.statuses,
            limit=body.limit,
            min_score=body.min_score,
            metadata_filter=body.metadata_filter,
        )
    )
    return {"matches": [m.model_dump(mode="json") for m in matches]}


@router.post("/memory/records:touch", dependencies=[WRITE])
async def touch(body: TouchIn, caller: Caller, domain: Domain):
    namespace = namespace_of(caller, body.namespace)
    await domain.engine.store.memory.touch(namespace, body.ids)
    return Response(status_code=204)
