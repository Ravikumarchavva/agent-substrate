"""/api/v1/statements: authorize, plan, sign and stream. Table preview and profile build on it.

Arrow results pass through unchanged from the worker. JSON is produced in one pass only for
clients that ask for it (the browser), bounded by `max_rows`.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
import orjson
import pyarrow as pa
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from substrate_data.api.limits import check_budget, check_concurrency
from substrate_data.api.routes.catalog import visible_table
from substrate_data.api.services import Services, arrow_json, principal, services
from substrate_data.auth.principal import Principal
from substrate_data.core.errors import Problem, unavailable
from substrate_data.sql.planner import PlannedStatement

router = APIRouter(prefix="/api/v1", tags=["statements"])
Caller = Annotated[Principal, Depends(principal)]
Svc = Annotated[Services, Depends(services)]
ARROW = "application/vnd.apache.arrow.stream"
ACTIVE_LIMIT = 10_000


class StatementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    sql: str = Field(min_length=1, max_length=100_000)
    catalog: str = Field(default="main", pattern=r"^[a-z_][a-z0-9_]{0,63}$")
    schema_: str = Field(default="default", alias="schema", pattern=r"^[a-z_][a-z0-9_]{0,63}$")
    compute_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    max_rows: int | None = Field(default=None, ge=1, le=10_000_000)
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    # Optional client-chosen id, so a client can cancel a statement that is still running.
    statement_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ActiveStatements:
    """Which worker runs which statement, for cancellation (bounded, process-local)."""

    def __init__(self) -> None:
        self.items: OrderedDict[str, tuple[str, str, str, str | None]] = OrderedDict()

    def add(
        self, statement_id: str, owner: str, url: str, resource_id: str, tenant: str | None = None
    ) -> None:
        self.items[statement_id] = (owner, url, resource_id, tenant)
        while len(self.items) > ACTIVE_LIMIT:
            self.items.popitem(last=False)

    def count(self, owner: str) -> int:
        return sum(1 for item in list(self.items.values()) if item[0] == owner)

    def count_tenant(self, tenant: str) -> int:
        return sum(1 for item in list(self.items.values()) if item[3] == tenant)

    def pop(self, statement_id: str) -> None:
        self.items.pop(statement_id, None)


async def candidate_workers(
    svc: Services, caller: Principal, compute_id: str | None
) -> list[dict[str, Any]]:
    resources = [
        r
        for r in await svc.controller.resources()
        if r["role"] in {"sql", "jobs"} and r["state"] == "ready"
    ]
    if compute_id:
        resources = [r for r in resources if r["id"] == compute_id]
        if not resources:
            raise unavailable("The requested compute is not attached or not ready")
    allowed = [r for r in resources if svc.store.can_use_compute(caller, r["id"])]
    if not allowed:
        if resources:
            raise Problem(403, "forbidden", "You may not use the attached SQL compute")
        raise unavailable("No SQL compute is attached and ready")
    # SQL warehouses first; all-purpose compute serves SQL only when no warehouse is usable.
    warehouses = [r for r in allowed if r["role"] == "sql"]
    return sorted(warehouses or allowed, key=lambda r: r["name"])


def rendezvous(workers: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Highest-random-weight choice: a key keeps its worker as long as that worker is ready, and
    adding or losing a worker only moves the keys that worker owned."""
    return max(workers, key=lambda w: hashlib.sha256(f"{key}|{w['id']}".encode()).digest())


async def plan(
    svc: Services, caller: Principal, body: StatementRequest, max_rows: int | None, request: Request
) -> tuple[PlannedStatement, dict[str, Any]]:
    tenant = caller.context.get("tenant_id")
    check_concurrency(request.app.state.active, svc.settings, caller.id, tenant)
    check_budget(svc.store, svc.settings, caller.id)
    workers = await candidate_workers(svc, caller, body.compute_id)
    try:
        planned = await run_in_threadpool(
            svc.planner.plan,
            caller,
            body.sql,
            catalog=body.catalog,
            schema=body.schema_,
            max_rows=max_rows,
            max_bytes=svc.settings.max_result_bytes,
            timeout_seconds=body.timeout_seconds or svc.settings.statement_timeout_seconds,
            resource_id=lambda affinity: rendezvous(workers, affinity)["id"],
            statement_id=body.statement_id,
        )
    except Problem as exc:
        await run_in_threadpool(
            svc.store.record,
            "statements",
            {
                "principal": caller.id,
                "actor": caller.actor,
                "context": caller.context,
                "sql": body.sql,
                "status": "denied" if exc.status in {403, 404} else "failed",
                "error": exc.detail[:1000],
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        raise
    worker = next(w for w in workers if w["id"] == planned.resource_id)
    await run_in_threadpool(
        svc.store.record,
        "statements",
        {
            "id": planned.statement_id,
            "principal": caller.id,
            "actor": caller.actor,
            "context": caller.context,
            "sql": body.sql,
            "status": "running",
            "resource_id": worker["id"],
            "tables": planned.tables,
        },
    )
    request.app.state.active.add(
        planned.statement_id, caller.id, worker["url"], worker["id"], tenant
    )
    await run_in_threadpool(svc.store.add_usage, caller.id, planned.bytes)
    return planned, worker


async def open_stream(
    svc: Services, planned: PlannedStatement, worker: dict[str, Any], request: Request
) -> httpx.Response:
    outgoing = svc.workers.build_request(
        "POST",
        worker["url"] + "/internal/v1/statements",
        headers={
            "Authorization": "Ticket " + planned.ticket,
            "X-Request-Id": request.state.request_id,
        },
    )
    try:
        response = await svc.workers.send(outgoing, stream=True)
    except httpx.HTTPError:
        raise unavailable("The attached SQL compute is unreachable") from None
    if response.status_code != 200:
        body = await response.aread()
        await response.aclose()
        try:
            problem = orjson.loads(body)
        except orjson.JSONDecodeError:
            problem = {}
        raise Problem(
            response.status_code if response.status_code < 500 else 502,
            problem.get("type", "compute-error").rsplit("/", 1)[-1],
            problem.get("detail", "Statement failed on attached compute"),
            **({"retry_after": 1} if response.status_code == 429 else {}),
        )
    return response


def finish(
    svc: Services,
    request: Request,
    statement_id: str,
    started: float,
    status: str,
    rows: int | None = None,
    error: str | None = None,
) -> None:
    request.app.state.active.pop(statement_id)
    svc.store.update(
        "statements",
        statement_id,
        {
            "status": status,
            "row_count": rows,
            "error": error,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


async def run_json(
    svc: Services, caller: Principal, body: StatementRequest, request: Request, limit: int
) -> tuple[bytes, dict[str, Any]]:
    started = time.perf_counter()
    planned, worker = await plan(svc, caller, body, limit, request)
    planned_at = time.perf_counter()
    try:
        response = await open_stream(svc, planned, worker, request)
        first_byte = time.perf_counter()
        try:
            data = await response.aread()
        finally:
            await response.aclose()
        table = pa.ipc.open_stream(data).read_all()
    except Problem as exc:
        await run_in_threadpool(
            finish, svc, request, planned.statement_id, started, "failed", None, exc.detail
        )
        raise
    except httpx.HTTPError, pa.ArrowInvalid:
        await run_in_threadpool(
            finish,
            svc,
            request,
            planned.statement_id,
            started,
            "failed",
            None,
            "Result stream ended early",
        )
        raise Problem(502, "result-incomplete", "The result stream ended early") from None
    payload, rows, _ = arrow_json(table, limit)
    await run_in_threadpool(finish, svc, request, planned.statement_id, started, "succeeded", rows)
    meta = {
        "statement_id": planned.statement_id,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "files_scanned": planned.files,
        "result_cache": response.headers.get("x-result-cache"),
        "timing_ms": {
            "plan": round((planned_at - started) * 1000, 2),
            "compute": round((first_byte - planned_at) * 1000, 2),
        },
    }
    return payload, meta


def server_timing(timings: dict[str, float]) -> str:
    return ", ".join(f"{name};dur={value}" for name, value in timings.items())


def json_response(payload: bytes, meta: dict[str, Any]) -> Response:
    # Splice metadata into the already-serialized body instead of re-encoding the rows.
    head = orjson.dumps(meta)[:-1] + b","
    return Response(
        head + payload[1:],
        media_type="application/json",
        headers={"Server-Timing": server_timing(meta.get("timing_ms", {}))},
    )


@router.post("/statements")
async def execute(body: StatementRequest, svc: Svc, caller: Caller, request: Request):
    wants_arrow = ARROW in request.headers.get("accept", "")
    if not wants_arrow:
        limit = min(body.max_rows or svc.settings.max_rows, 100_000)
        payload, meta = await run_json(svc, caller, body, request, limit)
        return json_response(payload, meta)

    started = time.perf_counter()
    planned, worker = await plan(svc, caller, body, body.max_rows, request)
    planned_at = time.perf_counter()
    try:
        response = await open_stream(svc, planned, worker, request)
        first_byte = time.perf_counter()
    except Problem as exc:
        await run_in_threadpool(
            finish, svc, request, planned.statement_id, started, "failed", None, exc.detail
        )
        raise

    async def passthrough() -> AsyncIterator[bytes]:
        status, error = "failed", "Client disconnected"
        held: bytes | None = None
        try:
            async for chunk in response.aiter_raw():
                if held is not None:
                    yield held
                held = chunk
            status, error = "succeeded", None
        except httpx.HTTPError:
            error = "Result stream ended early"
        finally:
            await response.aclose()
        # Record the outcome before the final chunk leaves, so a client that reads the whole
        # stream and then lists its history never sees this statement as still running.
        await run_in_threadpool(
            finish, svc, request, planned.statement_id, started, status, None, error
        )
        if held is not None and status == "succeeded":
            yield held

    timings = {
        "plan": round((planned_at - started) * 1000, 2),
        "compute": round((first_byte - planned_at) * 1000, 2),
    }
    return StreamingResponse(
        passthrough(),
        media_type=ARROW,
        headers={
            "X-Statement-Id": planned.statement_id,
            "Server-Timing": server_timing(timings),
            "X-Result-Cache": response.headers.get("x-result-cache", "miss"),
        },
    )


@router.get("/statements")
def list_statements(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(50, ge=1, le=500),
    everyone: bool = Query(False, alias="all"),
):
    if everyone and not caller.is_admin:
        raise Problem(403, "forbidden", "Only admins can list every principal's statements")
    rows = svc.store.page(
        "statements",
        "1=1" if everyone else "principal=?",
        [] if everyone else [caller.id],
        page_token,
        page_size + 1,
        ("tables", "context"),
    )
    more = len(rows) > page_size
    rows = rows[:page_size]
    token = f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more else None
    return {"statements": rows, "next_page_token": token}


@router.get("/statements/{statement_id}")
def get_statement(statement_id: str, svc: Svc, caller: Caller):
    row = svc.store.get("statements", statement_id, ("tables", "context"))
    if row["principal"] != caller.id and not caller.is_admin:
        raise Problem(404, "not-found", "Statement was not found")
    return row


@router.post("/statements/{statement_id}:cancel")
async def cancel(statement_id: str, svc: Svc, caller: Caller, request: Request):
    active = request.app.state.active.items.get(statement_id)
    if active is None:
        return {"interrupted": False}
    owner, url, resource_id, _ = active
    if owner != caller.id and not caller.is_admin:
        raise Problem(404, "not-found", "Statement was not found")
    ticket = svc.planner.cancel_ticket(caller, statement_id, resource_id)
    try:
        response = await svc.workers.post(
            f"{url}/internal/v1/statements/{statement_id}:cancel",
            headers={"Authorization": "Ticket " + ticket},
            timeout=5,
        )
        return {"interrupted": bool(response.status_code == 200 and response.json()["interrupted"])}
    except httpx.HTTPError:
        return {"interrupted": False}


# ------------------------------------------------------------------ table preview and profile
def quoted(*parts: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


@router.get("/catalogs/{catalog}/schemas/{schema}/tables/{table}/preview")
async def preview(
    catalog: str,
    schema: str,
    table: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000_000),
    order_by: str | None = Query(None, max_length=128),
    descending: bool = False,
    compute_id: str | None = None,
):
    record = visible_table(svc, caller, catalog, schema, table)
    order = ""
    if order_by:
        if order_by not in {c["name"] for c in record["columns"]}:
            raise Problem(400, "invalid-request", "Unknown order_by column")
        order = f" ORDER BY {quoted(order_by)} {'DESC' if descending else 'ASC'}"
    sql = f"SELECT * FROM {quoted(catalog, schema, table)}{order} LIMIT {limit} OFFSET {offset}"
    payload, meta = await run_json(
        svc, caller, StatementRequest(sql=sql, compute_id=compute_id), request, limit
    )
    meta.update(total=record["row_count"], offset=offset)
    return json_response(payload, meta)


NUMERIC = ("INTEGER", "BIGINT", "FLOAT", "DOUBLE", "DECIMAL", "SMALLINT", "TINYINT", "HUGEINT")


@router.get("/catalogs/{catalog}/schemas/{schema}/tables/{table}/profile")
async def profile(
    catalog: str,
    schema: str,
    table: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    sample_rows: int = Query(100_000, ge=1000, le=10_000_000),
):
    """Column statistics over a bounded sample, computed in one scan on attached compute."""
    record = visible_table(svc, caller, catalog, schema, table)
    columns = record["columns"][:200]
    parts = ["COUNT(*) AS n"]
    for index, column in enumerate(columns):
        name = quoted(column["name"])
        parts += [
            f"COUNT({name}) AS c{index}_values",
            f"APPROX_COUNT_DISTINCT({name}) AS c{index}_distinct",
        ]
        if column["type"].startswith(NUMERIC):
            parts += [
                f"MIN({name}) AS c{index}_min",
                f"MAX({name}) AS c{index}_max",
                f"AVG({name}) AS c{index}_mean",
                f"STDDEV({name}) AS c{index}_std",
            ]
        elif column["type"] in {"DATE", "TIMESTAMP", "TIMESTAMPTZ"}:
            parts += [f"MIN({name}) AS c{index}_min", f"MAX({name}) AS c{index}_max"]
        elif column["type"] == "VARCHAR":
            parts += [
                f"MIN(LENGTH({name})) AS c{index}_min_length",
                f"MAX(LENGTH({name})) AS c{index}_max_length",
            ]
    sql = (
        f"SELECT {', '.join(parts)} FROM "
        f"(SELECT * FROM {quoted(catalog, schema, table)} LIMIT {sample_rows})"
    )
    payload, meta = await run_json(svc, caller, StatementRequest(sql=sql), request, 1)
    result = orjson.loads(payload)
    values = dict(zip([c["name"] for c in result["columns"]], result["rows"][0]))
    examined = values["n"]
    stats = []
    for index, column in enumerate(columns):
        present = values.get(f"c{index}_values", 0)
        entry = {
            "name": column["name"],
            "type": column["type"],
            "nulls": examined - present,
            "null_percent": round((examined - present) / examined * 100, 2) if examined else 0,
            "distinct_approx": values.get(f"c{index}_distinct"),
        }
        for key in ("min", "max", "mean", "std", "min_length", "max_length"):
            if f"c{index}_{key}" in values:
                entry[key] = values[f"c{index}_{key}"]
        stats.append(entry)
    return {
        "rows_examined": examined,
        "total_rows": record["row_count"],
        "sampled": examined < record["row_count"],
        "columns": stats,
        "duration_ms": meta["duration_ms"],
    }
