"""/api/v1/pipelines: a table transformed into another by a batch job."""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

import pyarrow as pa
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from loamz.engine.errors import InvalidRequestError, NotFoundError
from loamz.server.data.features.catalog.api import (
    ARROW_TYPES,
    PartitionSpec,
    create_managed_table,
    visible_schema,
    visible_table,
)
from loamz.server.data.features.tables.iceberg import table_service
from loamz.server.data.features.tables.materialized import forbid_writes
from loamz.server.data.features.tables.publications import job_publisher
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import not_found
from loamz.server.data.kernel.http import Caller, Svc

router = APIRouter(prefix="/api/v1")
FULL_NAME = r"^[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}$"


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


def pipeline_public(row: dict, svc: Container) -> dict:
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
        raise InvalidRequestError(f"Unknown source columns: {sorted(referenced - names)}")
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
    except NotFoundError:
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
    plan = await run_in_threadpool(svc.get(table_service).plan, source, [])
    iceberg = await run_in_threadpool(svc.get(table_service).load, target, True)
    run = svc.store.record(
        "pipeline_runs", {"pipeline_id": pipeline_id, "principal": caller.id, "status": "queued"}
    )
    write_id = uuid4().hex
    job_id = await svc.get(job_publisher).submit(
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
