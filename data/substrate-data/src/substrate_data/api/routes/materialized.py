"""/api/v1 materialized views: a query whose result is stored as a table and refreshed on demand.

A materialized view is an Iceberg table plus its definition, so reads are ordinary pruned table
scans (no recomputation). Refresh recomputes the whole definition on attached compute:

* The control plane plans and authorizes the definition as the view's *definer* (the creator as they
  were when they created it, as with logical views) and signs a `materialize` ticket. The control plane
  reads no data.
* A jobs worker verifies the ticket, runs the SQL on its locked-down engine and writes Parquet
  straight into the table's location; the control plane then commits the files in one atomic,
  idempotent Iceberg commit, so readers see the old rows or the new rows, never a mix.

Readers need `SELECT` on the materialized view only. Staleness is exposed, never hidden: `stale` is
true when a source table has a newer snapshot than the one the last refresh read.
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pyarrow as pa
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from substrate_data.api.routes.catalog import (
    Caller,
    PartitionSpec,
    Svc,
    audit,
    create_managed_table,
    freshness,
    visible_schema,
    visible_table,
)
from substrate_data.api.routes.maintenance import public as run_public
from substrate_data.api.routes.maintenance import running
from substrate_data.api.routes.statements import StatementRequest, finish, open_stream, plan
from substrate_data.api.services import Services
from substrate_data.auth.principal import Principal
from substrate_data.catalog.store import NAME, now
from substrate_data.core.errors import Problem, conflict
from substrate_data.sql.analysis import incremental_source
from substrate_data.sql.planner import definer_of

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["materialized views"])
TICKET_SECONDS = 900
JOB_SECONDS = 6 * 3600


class MaterializedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME)
    sql: str = Field(min_length=1, max_length=100_000)
    comment: str | None = Field(default=None, max_length=1000)
    partitioning: list[PartitionSpec] = Field(default_factory=list, max_length=8)
    # Refresh by itself at most this often (minutes), and only when a source has changed.
    refresh_every_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 7)
    # Process only source files added since the last refresh and append (see docs/OPERATIONS.md).
    # Allowed for a plain filter/projection of one table; falls back to a full refresh whenever the
    # source was rewritten rather than appended to.
    incremental: bool = False


def forbid_writes(table: dict[str, Any]) -> None:
    if table.get("materialized"):
        raise conflict(
            f"{table['name']} is a materialized view: change its definition's sources, "
            "then refresh it"
        )


def normalize(schema: pa.Schema) -> pa.Schema:
    """Iceberg v2 stores microseconds."""
    fields = []
    for field in schema:
        if pa.types.is_timestamp(field.type) and field.type.unit == "ns":
            field = field.with_type(pa.timestamp("us", tz=field.type.tz))
        fields.append(field)
    return pa.schema(fields)


async def probe_schema(
    svc: Services,
    caller: Principal,
    sql: str,
    catalog: str,
    schema: str,
    request: Request,
) -> pa.Schema:
    """Run the definition with LIMIT 0 as `caller`: validates it, proves they may read what it
    touches, and returns the exact Arrow schema its result will have."""
    body = StatementRequest(
        sql=f"SELECT * FROM (\n{sql}\n) AS definition LIMIT 0", catalog=catalog, schema=schema
    )
    started = time.perf_counter()
    planned, worker = await plan(svc, caller, body, 1, request)
    try:
        response = await open_stream(svc, planned, worker, request)
        try:
            data = await response.aread()
        finally:
            await response.aclose()
        result = pa.ipc.open_stream(data).read_all().schema
    except BaseException as exc:
        await run_in_threadpool(
            finish, svc, request, planned.statement_id, started, "failed", None, str(exc)[:500]
        )
        raise
    await run_in_threadpool(finish, svc, request, planned.statement_id, started, "succeeded", 0)
    return result


async def start_refresh(
    svc: Services,
    publisher: Any,
    table: dict[str, Any],
    requested_by: Principal,
    *,
    full: bool = False,
) -> dict[str, Any]:
    """Recompute (or, when allowed, extend) a materialized view as its definer."""
    info = table["materialized"]
    if running(svc, table["id"]):
        raise conflict("A refresh or maintenance operation is already running on this view")
    mode, restrict = "overwrite", None
    if info.get("incremental") and not full and info.get("refreshed_at") and info.get("sources"):
        ((source_name, since),) = info["sources"].items()
        source = svc.store.tables_by_names([tuple(source_name.split("."))]).get(
            tuple(source_name.split("."))
        )
        added = (
            await run_in_threadpool(svc.tables.appended_since, source, since) if source else None
        )
        if added is not None:
            if not added:  # nothing new since the last refresh
                run = svc.store.record(
                    "maintenance_runs",
                    {
                        "table_id": table["id"],
                        "principal": requested_by.id,
                        "kind": "refresh",
                        "params": {"mode": "incremental"},
                        "status": "skipped",
                        "error": "No new data",
                        "finished_at": now(),
                    },
                )
                return svc.store.get("maintenance_runs", run["id"], ("params",))
            mode, restrict = "append", {source_name: set(added)}
    definer = definer_of({"owner": table["owner"], "definer": info["definer"]})
    try:
        planned = await run_in_threadpool(
            lambda: svc.planner.plan(
                definer,
                info["sql"],
                catalog=table["catalog_name"],
                schema=table["schema_name"],
                max_rows=None,
                max_bytes=2**50,
                timeout_seconds=JOB_SECONDS,
                resource_id="jobs",
                op="materialize",
                ttl=TICKET_SECONDS,
                cache=False,
                restrict=restrict,
            )
        )
    except Problem as exc:
        if exc.status in {403, 404}:
            raise Problem(
                403,
                "materialized-view-definer-access",
                "The view's owner can no longer read what its definition reads: "
                "recreate the view as someone who can",
            ) from None
        raise
    iceberg = await run_in_threadpool(svc.tables.load, table, True)
    run = svc.store.record(
        "maintenance_runs",
        {
            "table_id": table["id"],
            "principal": requested_by.id,
            "kind": "refresh",
            "params": {"mode": "incremental" if mode == "append" else "full"},
            "status": "running",
            "files_in": planned.files,
            "bytes_before": planned.bytes,
        },
    )
    write_id = uuid4().hex
    job_id = await publisher.submit(
        "materialized.refresh",
        "materialize",
        {
            "ticket": planned.ticket,
            "schema_ipc": info["schema_ipc"],
            "location": iceberg.location(),
            "write_id": write_id,
            "partitioning": [
                {"column": p["column"], "transform": p["transform"]} for p in table["partitioning"]
            ],
        },
        {
            "run_id": run["id"],
            "table_id": table["id"],
            "write_id": write_id,
            "sources": planned.sources,
            "mode": mode,
            "principal": requested_by.id,
        },
        max_attempts=1,  # the ticket is single-use and short-lived: a retry would be refused
        queue_timeout_seconds=TICKET_SECONDS - 60,
    )
    svc.store.update("maintenance_runs", run["id"], {"job_id": job_id})
    svc.store.update_table_state(
        table["id"], {"materialized": {**info, "status": "refreshing", "error": None}}
    )
    return svc.store.get("maintenance_runs", run["id"], ("params",))


def is_stale(svc: Services, table: dict[str, Any]) -> bool:
    fresh = freshness(svc, table)
    return bool(fresh and fresh["stale"])


async def refresh_due(svc: Services, publisher: Any) -> int:
    """Start every scheduled materialized view that is due and whose sources have changed.
    Returns how many were started. Called by the publishing replica on a timer."""
    started = 0
    for table in await run_in_threadpool(svc.store.materialized_tables):
        info = table["materialized"]
        every = info.get("refresh_every_minutes")
        if not every or info["status"] == "refreshing":
            continue
        last = info.get("refreshed_at") or info.get("attempted_at")
        if last and datetime.fromisoformat(last) + timedelta(minutes=every) > datetime.now(UTC):
            continue
        if info.get("refreshed_at") and not is_stale(svc, table):
            continue
        try:
            owner = definer_of({"owner": table["owner"], "definer": info["definer"]})
            await start_refresh(svc, publisher, table, owner)
        except Problem as exc:
            logger.info("Scheduled refresh of %s skipped: %s", table["name"], exc.detail)
            svc.store.update_table_state(
                table["id"],
                {"materialized": {**info, "attempted_at": now(), "error": exc.detail[:500]}},
            )
        else:
            started += 1
    return started


def _encode(schema: pa.Schema) -> str:
    return base64.b64encode(schema.serialize().to_pybytes()).decode()


@router.post("/catalogs/{catalog}/schemas/{schema}/materialized-views", status_code=202)
async def create_materialized_view(
    catalog: str,
    schema: str,
    body: MaterializedViewCreate,
    svc: Svc,
    caller: Caller,
    request: Request,
    response: Response,
):
    parent = visible_schema(svc, caller, catalog, schema)
    svc.store.require(caller, "schema", parent, "CREATE_TABLE")
    definition = body.sql.strip().rstrip(";").strip()
    if body.incremental:
        incremental_source(definition, catalog, schema)  # refuses anything that cannot be correct
    arrow = normalize(await probe_schema(svc, caller, definition, catalog, schema, request))
    record = await run_in_threadpool(
        create_managed_table,
        svc,
        parent,
        body.name,
        caller.id,
        body.comment,
        arrow,
        [p.model_dump() for p in body.partitioning],
        None,
    )
    table = svc.store.table(catalog, schema, body.name)
    svc.store.update_table_state(
        table["id"],
        {
            "materialized": {
                "sql": definition,
                "definer": {
                    "subject": caller.subject,
                    "groups": sorted(caller.groups),
                    "is_admin": caller.is_admin,
                },
                "schema_ipc": _encode(arrow),
                "refresh_every_minutes": body.refresh_every_minutes,
                "incremental": body.incremental,
                "status": "refreshing",
                "refreshed_at": None,
                "sources": {},
                "error": None,
            }
        },
    )
    table = svc.store.table(catalog, schema, body.name)
    run = await start_refresh(svc, request.app.state.publisher, table, caller)
    audit(svc, request, caller, "materialized_view.create", record["full_name"])
    response.headers["Location"] = f"/api/v1/catalogs/{catalog}/schemas/{schema}/tables/{body.name}"
    return {"table": record["full_name"], "refresh": run_public(run)}


@router.post("/catalogs/{catalog}/schemas/{schema}/tables/{table}:refresh", status_code=202)
async def refresh_materialized_view(
    catalog: str,
    schema: str,
    table: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    full: bool = False,
):
    record = visible_table(svc, caller, catalog, schema, table)
    if not record.get("materialized"):
        raise Problem(409, "not-a-materialized-view", f"{table} is not a materialized view")
    svc.store.require(caller, "table", record, "MODIFY")
    run = await start_refresh(svc, request.app.state.publisher, record, caller, full=full)
    audit(svc, request, caller, "materialized_view.refresh.start", f"{catalog}.{schema}.{table}")
    return run_public(run)
