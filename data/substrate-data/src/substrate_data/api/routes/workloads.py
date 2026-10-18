"""/api/v1 pipelines, collections (retrieval), compute resources, jobs, audit and identity."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import uuid4

import orjson
import pyarrow as pa
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from substrate_data.api.routes.catalog import (
    ARROW_TYPES,
    PartitionSpec,
    create_managed_table,
    visible_schema,
    visible_table,
)
from substrate_data.api.routes.materialized import forbid_writes
from substrate_data.api.services import Services, principal, services
from substrate_data.auth.principal import Principal
from substrate_data.core.errors import Problem, conflict, forbidden, not_found

router = APIRouter(prefix="/api/v1")
Caller = Annotated[Principal, Depends(principal)]
Svc = Annotated[Services, Depends(services)]
FULL_NAME = r"^[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}$"


# ------------------------------------------------------------------ identity
@router.get("/me", tags=["identity"])
def me(caller: Caller):
    return {
        "principal": caller.id,
        "name": caller.name,
        "kind": caller.kind,
        "email": caller.email,
        "groups": sorted(caller.groups),
        "is_admin": caller.is_admin,
        "actor": caller.actor,
    }


@router.get("/overview", tags=["identity"])
def overview(svc: Svc, caller: Caller):
    with svc.store.connection() as db:
        rows = db.execute(
            svc.store.TABLE_SELECT + "ORDER BY t.updated_at DESC LIMIT 500"
        ).fetchall()
    from substrate_data.catalog.store import TABLE_JSON, decode

    tables = [decode(r, TABLE_JSON) for r in rows]
    visible = [t for t in tables if svc.store.can_browse(caller, "table", t)]
    recent = svc.store.page(
        "statements", "principal=?", [caller.id], None, 8, ("tables", "context")
    )
    return {
        "tables": len(visible),
        "rows": sum(t["row_count"] for t in visible),
        "size_bytes": sum(t["size_bytes"] for t in visible),
        "recent_tables": [
            {
                "full_name": f"{t['catalog_name']}.{t['schema_name']}.{t['name']}",
                "row_count": t["row_count"],
                "updated_at": t["updated_at"],
            }
            for t in visible[:8]
        ],
        "recent_statements": recent,
    }


@router.get("/audit-events", tags=["identity"])
def audit_events(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(100, ge=1, le=1000),
):
    if not caller.is_admin:
        raise forbidden("Only admins can read the audit log")
    before = int(page_token) if page_token and page_token.isdigit() else None
    rows = svc.store.audit_events(before, page_size + 1)
    more = len(rows) > page_size
    rows = rows[:page_size]
    return {"events": rows, "next_page_token": str(rows[-1]["id"]) if more else None}


def cef(event: dict) -> str:
    """ArcSight Common Event Format, understood by most SIEMs."""

    def esc(value: object, header: bool = False) -> str:
        text = str(value if value is not None else "")
        text = text.replace("\\", "\\\\").replace(
            "|" if header else "=", "\\|" if header else "\\="
        )
        return text.replace("\n", " ")

    severity = 7 if event["outcome"] != "success" else 3
    extension = (
        f"rt={esc(event['at'])} suser={esc(event['principal'])} "
        f"duser={esc(event['actor'])} cs1Label=securable cs1={esc(event['securable'])} "
        f"outcome={esc(event['outcome'])} externalId={event['id']} "
        f"cs2Label=hash cs2={esc(event.get('hash'))}"
    )
    return f"CEF:0|Substrate|Data|1|{esc(event['action'], True)}|{esc(event['action'], True)}|{severity}|{extension}"


@router.get("/audit-events:export", tags=["identity"])
def export_audit_events(
    svc: Svc,
    caller: Caller,
    after: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
    format: Literal["ndjson", "cef"] = "ndjson",
):
    """For log collectors: oldest first, resumable. Pass the last `id` you received as `after`.
    The `X-Next-After` header carries it; an empty body means you are caught up."""
    if not caller.is_admin:
        raise forbidden("Only admins can read the audit log")
    rows = svc.store.audit_since(after, limit)
    if format == "cef":
        body = "".join(cef(r) + "\n" for r in rows)
        media = "text/plain"
    else:
        body = "".join(orjson.dumps(r).decode() + "\n" for r in rows)
        media = "application/x-ndjson"
    return Response(
        body,
        media_type=media,
        headers={"X-Next-After": str(rows[-1]["id"] if rows else after)},
    )


@router.get("/audit-events:verify", tags=["identity"])
def verify_audit_events(svc: Svc, caller: Caller):
    """Check that no audit event has been altered or removed from the middle of the log."""
    if not caller.is_admin:
        raise forbidden("Only admins can read the audit log")
    return svc.store.verify_audit()


# ------------------------------------------------------------------ pipelines
class PipelineDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str] = Field(default_factory=list, max_length=500)
    filter_column: str | None = Field(default=None, max_length=128)
    operator: Literal["eq", "gt", "lt"] = "eq"
    value: str | float | int | None = None
    drop_nulls: bool = False
    unique: bool = False


class PipelineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    source: str = Field(pattern=FULL_NAME)
    target: str = Field(pattern=FULL_NAME)
    definition: PipelineDefinition = Field(default_factory=PipelineDefinition)
    partitioning: list[PartitionSpec] = Field(default_factory=list, max_length=8)


def pipeline_public(row: dict, svc: Services) -> dict:
    source = svc.store.table_by_id(row["source_table_id"])
    return {
        "id": row["id"],
        "name": row["name"],
        "owner": row["owner"],
        "source": f"{source['catalog_name']}.{source['schema_name']}.{source['name']}",
        "target": row["target_name"],
        "definition": row["definition"],
        "created_at": row["created_at"],
    }


@router.post("/pipelines", status_code=201, tags=["pipelines"])
def create_pipeline(
    body: PipelineCreate, svc: Svc, caller: Caller, request: Request, response: Response
):
    source = visible_table(svc, caller, *body.source.split("."))
    svc.store.require(caller, "table", source, "SELECT")
    names = {c["name"] for c in source["columns"]}
    referenced = set(body.definition.columns) | ({body.definition.filter_column} - {None})
    if referenced - names:
        raise Problem(
            400, "invalid-request", f"Unknown source columns: {sorted(referenced - names)}"
        )
    catalog, schema, _ = body.target.split(".")
    visible_schema(svc, caller, catalog, schema)
    row = svc.store.record(
        "pipelines",
        {
            "name": body.name,
            "owner": caller.id,
            "source_table_id": source["id"],
            "target_schema_id": svc.store.schema(catalog, schema)["id"],
            "target_name": body.target,
            "definition": {
                **body.definition.model_dump(),
                "partitioning": [p.model_dump() for p in body.partitioning],
            },
        },
    )
    svc.store.audit(caller, "pipeline.create", body.name, "success", request.state.request_id)
    response.headers["Location"] = f"/api/v1/pipelines/{row['id']}"
    return pipeline_public(svc.store.get("pipelines", row["id"], ("definition",)), svc)


@router.get("/pipelines", tags=["pipelines"])
def list_pipelines(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(50, ge=1, le=500),
):
    where, params = ("1=1", []) if caller.is_admin else ("owner=?", [caller.id])
    rows = svc.store.page("pipelines", where, params, page_token, page_size + 1, ("definition",))
    more = len(rows) > page_size
    rows = rows[:page_size]
    runs = svc.store.page("pipeline_runs", where.replace("owner", "principal"), params, None, 50)
    return {
        "pipelines": [pipeline_public(r, svc) for r in rows],
        "runs": runs,
        "next_page_token": f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more else None,
    }


@router.post("/pipelines/{pipeline_id}:run", status_code=202, tags=["pipelines"])
async def run_pipeline(pipeline_id: str, svc: Svc, caller: Caller, request: Request):
    row = svc.store.get("pipelines", pipeline_id, ("definition",))
    if row["owner"] != caller.id and not caller.is_admin:
        raise not_found("Pipeline")
    source = svc.store.table_by_id(row["source_table_id"])
    svc.store.require(caller, "table", source, "SELECT")
    catalog, schema, name = row["target_name"].split(".")
    definition = row["definition"]
    try:
        target = svc.store.table(catalog, schema, name)
        svc.store.require(caller, "table", target, "MODIFY")
        forbid_writes(target)
    except Problem as exc:
        if exc.status != 404:
            raise
        parent = svc.store.schema(catalog, schema)
        svc.store.require(caller, "schema", parent, "CREATE_TABLE")
        columns = [
            c
            for c in source["columns"]
            if not definition["columns"] or c["name"] in definition["columns"]
        ]
        arrow = pa.schema([(c["name"], ARROW_TYPES.get(c["type"], pa.string())) for c in columns])
        await run_in_threadpool(
            create_managed_table,
            svc,
            parent,
            name,
            caller.id,
            f"Output of pipeline {row['name']}",
            arrow,
            definition.get("partitioning", []),
            source["id"],
        )
        target = svc.store.table(catalog, schema, name)
    plan = await run_in_threadpool(svc.tables.plan, source, [])
    iceberg = await run_in_threadpool(svc.tables.load, target, True)
    run = svc.store.record(
        "pipeline_runs", {"pipeline_id": pipeline_id, "principal": caller.id, "status": "queued"}
    )
    write_id = uuid4().hex
    job_id = await request.app.state.publisher.submit(
        "pipeline.transform",
        "transform",
        {
            "source_files": [f.path for f in plan.files],
            "definition": {k: v for k, v in definition.items() if k != "partitioning"},
            "location": iceberg.location(),
            "write_id": write_id,
            "partitioning": target["partitioning"],
        },
        {"run_id": run["id"], "table_id": target["id"], "write_id": write_id},
    )
    svc.store.update("pipeline_runs", run["id"], {"job_id": job_id, "status": "running"})
    svc.store.audit(
        caller, "pipeline.run", row["name"], "success", request.state.request_id, job_id=job_id
    )
    return svc.store.get("pipeline_runs", run["id"])


# ------------------------------------------------------------------ collections (retrieval)
class CollectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=2_000_000)


def collection(svc: Services, caller: Principal, collection_id: str) -> dict:
    row = svc.store.get("collections", collection_id)
    if row["owner"] != caller.id and not caller.is_admin:
        raise not_found("Collection")
    return row


@router.post("/collections", status_code=201, tags=["retrieval"])
def create_collection(body: CollectionCreate, svc: Svc, caller: Caller):
    return svc.store.record("collections", {"name": body.name, "owner": caller.id})


@router.get("/collections", tags=["retrieval"])
def list_collections(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(100, ge=1, le=500),
):
    where, params = ("1=1", []) if caller.is_admin else ("owner=?", [caller.id])
    rows = svc.store.page("collections", where, params, page_token, page_size + 1)
    more = len(rows) > page_size
    rows = rows[:page_size]
    return {
        "collections": rows,
        "next_page_token": f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more else None,
    }


@router.post("/collections/{collection_id}/documents", status_code=202, tags=["retrieval"])
async def add_document(
    collection_id: str, body: DocumentCreate, svc: Svc, caller: Caller, request: Request
):
    collection(svc, caller, collection_id)
    document_id = uuid4().hex
    job_id = await request.app.state.publisher.submit(
        "document.chunk",
        "chunk",
        {"text": body.text},
        {"collection_id": collection_id, "document_id": document_id, "title": body.title},
    )
    return {"document_id": document_id, "job_id": job_id, "status": "queued"}


@router.get("/collections/{collection_id}/chunks", tags=["retrieval"])
def chunks(collection_id: str, svc: Svc, caller: Caller):
    collection(svc, caller, collection_id)
    return {"chunks": svc.store.list_chunks(collection_id, 200)}


@router.get("/collections/{collection_id}/search", tags=["retrieval"])
def search(
    collection_id: str, svc: Svc, caller: Caller, q: str = Query(min_length=1, max_length=500)
):
    collection(svc, caller, collection_id)
    tokens = re.findall(r"\w+", q)[:30]
    if not tokens:
        return {"results": []}
    return {"results": svc.store.search_chunks(collection_id, tokens, 20)}


# ------------------------------------------------------------------ compute (attach only)
class ResourceAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
    role: Literal["sql", "jobs", "rag", "inference"]
    url: str = Field(min_length=1, max_length=2048)
    token: str | None = Field(default=None, min_length=32, max_length=512, repr=False)
    concurrency: int = Field(default=4, ge=1, le=64)
    max_queue: int = Field(default=100, ge=1, le=10000)


def admin(caller: Principal) -> None:
    if not caller.is_admin:
        raise forbidden("Only admins manage compute")


@router.get("/compute/resources", tags=["compute"])
async def resources(svc: Svc, caller: Caller):
    items = await svc.controller.resources()
    if not caller.is_admin:
        items = [r for r in items if svc.store.can_use_compute(caller, r["id"])]
    return {"resources": items}


@router.post("/compute/resources", status_code=201, tags=["compute"])
async def attach(
    body: ResourceAttach, svc: Svc, caller: Caller, request: Request, response: Response
):
    admin(caller)
    resource = await svc.controller.request("POST", "/resources", json=body.model_dump())
    # Attached compute is usable by every signed-in user unless an admin revokes it.
    svc.store.grant("compute", resource["id"], "group:account users", "USE_COMPUTE", caller.id)
    svc.store.audit(
        caller, "compute.attach", resource["id"], "success", request.state.request_id, url=body.url
    )
    response.headers["Location"] = f"/api/v1/compute/resources/{resource['id']}"
    return resource


@router.put("/compute/resources/{resource_id}", tags=["compute"])
async def update_resource(
    resource_id: str, body: ResourceAttach, svc: Svc, caller: Caller, request: Request
):
    admin(caller)
    resource = await svc.controller.request(
        "PUT", f"/resources/{resource_id}", json=body.model_dump()
    )
    svc.store.audit(caller, "compute.update", resource_id, "success", request.state.request_id)
    return resource


@router.post("/compute/resources/{resource_id}:{action}", tags=["compute"])
async def resource_action(
    resource_id: str,
    action: Literal["start", "stop", "delete"],
    svc: Svc,
    caller: Caller,
    request: Request,
):
    admin(caller)
    result = await svc.controller.request("POST", f"/resources/{resource_id}:{action}")
    svc.store.audit(caller, f"compute.{action}", resource_id, "success", request.state.request_id)
    return result


@router.get("/compute/resources/{resource_id}/events", tags=["compute"])
async def resource_events(resource_id: str, svc: Svc, caller: Caller):
    admin(caller)
    return await svc.controller.request("GET", f"/resources/{resource_id}/events")


@router.get("/jobs", tags=["compute"])
async def jobs(svc: Svc, caller: Caller, page_size: int = Query(100, ge=1, le=1000)):
    admin(caller)
    return await svc.controller.request("GET", "/jobs", params={"limit": page_size})


@router.get("/jobs/{job_id}", tags=["compute"])
async def job(job_id: str, svc: Svc, caller: Caller):
    admin(caller)
    return await svc.controller.request("GET", f"/jobs/{job_id}")


@router.post("/jobs/{job_id}:cancel", tags=["compute"])
async def cancel_job(job_id: str, svc: Svc, caller: Caller, request: Request):
    admin(caller)
    result = await svc.controller.request("POST", f"/jobs/{job_id}:cancel")
    svc.store.audit(caller, "job.cancel", job_id, "success", request.state.request_id)
    return result


__all__ = ["router", "conflict"]
