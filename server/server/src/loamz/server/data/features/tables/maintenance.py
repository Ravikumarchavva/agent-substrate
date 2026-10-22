"""/api/v1 table maintenance: rewrite a table's files so queries read less.

`:optimize` runs on attached compute as a batch job and the control plane swaps the files in one
atomic Iceberg commit. Two strategies:

* `compact` merges small files (the usual result of many small appends) into files near the target size.
* `sort` rewrites files ordered by the columns queries filter on. Tight per-file min/max statistics let
  the planner skip whole files and DuckDB skip row groups, which is the Iceberg equivalent of
  clustering. Sort the partitions you query; use `where` to limit the rewrite to some of them.

Only the files named at planning time are replaced, so data appended while a rewrite runs is kept.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from loamz.engine.errors import InvalidRequestError, NotFoundError, QuotaExceededError
from loamz.server.data.features.catalog.api import Caller, Svc, audit, visible_table
from loamz.server.data.features.tables.iceberg import table_service
from loamz.server.data.features.tables.publications import job_publisher
from loamz.server.data.kernel.errors import conflict
from loamz.server.data.kernel.metadata import now

router = APIRouter(prefix="/api/v1", tags=["maintenance"])
COMPACT_BELOW = 0.75  # files under this fraction of the target are compaction candidates


def utcnow() -> datetime:
    return datetime.now(UTC)


ACTIVE = {"queued", "running"}


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=128)
    op: Literal["eq", "ne", "gt", "ge", "lt", "le"] = "eq"
    value: str | int | float


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["compact", "sort", "zorder"] = "compact"
    sort_by: list[str] = Field(default_factory=list, max_length=8)
    # Parquet bloom filters on these columns: equality filters (WHERE id = 42) skip row groups that
    # cannot hold the value even when min/max statistics cannot tell.
    bloom_columns: list[str] = Field(default_factory=list, max_length=8)
    where: list[Filter] = Field(default_factory=list, max_length=8)
    target_file_mb: int = Field(default=256, ge=16, le=2048)

    @model_validator(mode="after")
    def sort_needs_columns(self) -> OptimizeRequest:
        if self.strategy in {"sort", "zorder"} and not self.sort_by:
            raise ValueError(f"strategy {self.strategy!r} needs sort_by")
        if self.strategy == "compact" and self.sort_by:
            raise ValueError("sort_by is only used by the 'sort' and 'zorder' strategies")
        if self.strategy == "zorder" and not 2 <= len(self.sort_by) <= 4:
            raise ValueError("zorder interleaves 2 to 4 columns; use 'sort' for one")
        return self


class ExpireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    older_than_hours: int = Field(default=24 * 7, ge=1, le=24 * 3650)
    retain_last: int = Field(default=3, ge=1, le=1000)
    remove_orphans: bool = False
    # Files unreferenced and older than this may be orphans; younger ones could belong to a write
    # that has not committed yet (jobs run up to six hours).
    orphan_min_age_hours: int = Field(default=24, ge=12, le=24 * 365)


def public(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"],
        "kind": run["kind"],
        "status": run["status"],
        "params": run["params"],
        "files_in": run["files_in"],
        "files_out": run["files_out"],
        "rows": run["rows"],
        "bytes_before": run["bytes_before"],
        "bytes_after": run["bytes_after"],
        "error": run["error"],
        "principal": run["principal"],
        "created_at": run["created_at"],
        "finished_at": run["finished_at"],
    }


def running(svc: Any, table_id: str) -> bool:
    with svc.store.connection() as db:
        return (
            db.execute(
                "SELECT 1 FROM maintenance_runs WHERE table_id=? AND status IN ('queued','running') "
                "LIMIT 1",
                (table_id,),
            ).fetchone()
            is not None
        )


@router.post("/catalogs/{catalog}/schemas/{schema}/tables/{table}:optimize", status_code=202)
async def optimize(
    catalog: str,
    schema: str,
    table: str,
    body: OptimizeRequest,
    svc: Svc,
    caller: Caller,
    request: Request,
    response: Response,
):
    record = visible_table(svc, caller, catalog, schema, table)
    svc.store.require(caller, "table", record, "MODIFY")
    names = {c["name"].lower() for c in record["columns"]}
    unknown = {
        c.lower() for c in [*body.sort_by, *body.bloom_columns, *(f.column for f in body.where)]
    } - names
    if unknown:
        raise InvalidRequestError(f"Unknown columns: {sorted(unknown)}")
    if running(svc, record["id"]):
        raise conflict("Another maintenance operation is already running on this table")
    plan = await run_in_threadpool(
        svc.get(table_service).plan, record, [(f.column, f.op, f.value) for f in body.where]
    )
    target = body.target_file_mb * 2**20
    files = list(plan.files)
    if body.strategy == "compact":
        files = [f for f in files if f.size < COMPACT_BELOW * target]
    input_bytes = sum(f.size for f in files)
    if body.strategy in {"sort", "zorder"} and input_bytes > svc.settings.sort_max_input_mb * 2**20:
        raise QuotaExceededError(
            f"Sorting {input_bytes / 2**30:.1f} GiB of Parquet at once needs more memory than one "
            f"worker should use (limit {svc.settings.sort_max_input_mb} MiB). Sort partition by "
            "partition: pass `where` to select one partition at a time."
        )
    params = body.model_dump()
    base = {
        "table_id": record["id"],
        "principal": caller.id,
        "kind": "optimize",
        "params": params,
        "files_in": len(files),
        "bytes_before": input_bytes,
    }
    if len(files) < (2 if body.strategy == "compact" else 1):
        run = svc.store.record(
            "maintenance_runs",
            {**base, "status": "skipped", "error": "Nothing to rewrite", "finished_at": None},
        )
        return public(run | {"files_out": None, "rows": None, "bytes_after": None})
    iceberg = await run_in_threadpool(svc.get(table_service).load, record, True)
    run = svc.store.record("maintenance_runs", {**base, "status": "running"})
    write_id = uuid4().hex
    old_files = [f.path for f in files]
    job_id = await svc.get(job_publisher).submit(
        "table.optimize",
        "optimize",
        {
            "source_files": old_files,
            "sort_by": body.sort_by,
            "zorder": body.strategy == "zorder",
            "bloom_columns": body.bloom_columns,
            "location": iceberg.location(),
            "write_id": write_id,
            "partitioning": [
                {"column": p["column"], "transform": p["transform"]} for p in record["partitioning"]
            ],
            "target_file_bytes": target,
        },
        {
            "run_id": run["id"],
            "table_id": record["id"],
            "write_id": write_id,
            "old_files": old_files,
            "principal": caller.id,
        },
    )
    svc.store.update("maintenance_runs", run["id"], {"job_id": job_id})
    audit(
        svc, request, caller, "table.optimize.start", f"{catalog}.{schema}.{table}", job_id=job_id
    )
    response.headers["Location"] = (
        f"/api/v1/catalogs/{catalog}/schemas/{schema}/tables/{table}/maintenance-runs/{run['id']}"
    )
    return public(svc.store.get("maintenance_runs", run["id"], ("params",)))


@router.post("/catalogs/{catalog}/schemas/{schema}/tables/{table}:expire", status_code=202)
async def expire(
    catalog: str,
    schema: str,
    table: str,
    body: ExpireRequest,
    svc: Svc,
    caller: Caller,
    request: Request,
):
    """Forget old snapshots (ending time travel to them) and delete the files only they used."""
    record = visible_table(svc, caller, catalog, schema, table)
    svc.store.require(caller, "table", record, "MODIFY")
    if running(svc, record["id"]):
        raise conflict("Another maintenance operation is already running on this table")
    now_ = utcnow()
    expired, unreferenced = await run_in_threadpool(
        svc.get(table_service).expire_snapshots,
        record,
        now_ - timedelta(hours=body.older_than_hours),
        body.retain_last,
    )
    orphans: list[str] = []
    if body.remove_orphans:
        orphans = await run_in_threadpool(
            svc.get(table_service).orphan_files,
            record,
            now_ - timedelta(hours=body.orphan_min_age_hours),
        )
    paths = sorted({*unreferenced, *orphans})[:50_000]
    run = svc.store.record(
        "maintenance_runs",
        {
            "table_id": record["id"],
            "principal": caller.id,
            "kind": "expire",
            "params": {**body.model_dump(), "snapshots_expired": expired},
            "status": "running" if paths else "succeeded",
            "files_in": len(paths),
            "files_out": 0,
            "finished_at": None if paths else now(),
        },
    )
    if paths:
        job_id = await svc.get(job_publisher).submit(
            "table.expire",
            "delete",
            {"paths": paths},
            {"run_id": run["id"], "table_id": record["id"], "principal": caller.id},
        )
        svc.store.update("maintenance_runs", run["id"], {"job_id": job_id})
    audit(
        svc,
        request,
        caller,
        "table.expire",
        f"{catalog}.{schema}.{table}",
        snapshots_expired=expired,
        files=len(paths),
    )
    return public(svc.store.get("maintenance_runs", run["id"], ("params",)))


@router.get("/catalogs/{catalog}/schemas/{schema}/tables/{table}/maintenance-runs")
def list_runs(
    catalog: str,
    schema: str,
    table: str,
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(50, ge=1, le=200),
):
    record = visible_table(svc, caller, catalog, schema, table)
    svc.store.require(caller, "table", record, "MODIFY")
    rows = svc.store.page(
        "maintenance_runs", "table_id=?", [record["id"]], page_token, page_size + 1, ("params",)
    )
    more = len(rows) > page_size
    rows = rows[:page_size]
    return {
        "runs": [public(r) for r in rows],
        "next_page_token": f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more else None,
    }


@router.get("/catalogs/{catalog}/schemas/{schema}/tables/{table}/maintenance-runs/{run_id}")
def get_run(catalog: str, schema: str, table: str, run_id: str, svc: Svc, caller: Caller):
    record = visible_table(svc, caller, catalog, schema, table)
    svc.store.require(caller, "table", record, "MODIFY")
    run = svc.store.get("maintenance_runs", run_id, ("params",))
    if run["table_id"] != record["id"]:
        raise NotFoundError("Maintenance run was not found")
    return public(run)
