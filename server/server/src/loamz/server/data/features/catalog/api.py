"""/api/v1/catalogs, schemas, tables and grants (Unity Catalog-style three-level namespace)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pyarrow as pa
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from loamz.engine.errors import InvalidRequestError
from loamz.server.data.features.tables.iceberg import duckdb_type, table_service
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import forbidden, not_found
from loamz.server.data.kernel.http import (
    Caller,
    Svc,
    decode_token,
    encode_token,
    page_size,
)
from loamz.server.data.kernel.metadata import ALL, NAME, PRIVILEGES
from loamz.server.data.kernel.principal import Principal

router = APIRouter(prefix="/api/v1", tags=["catalog"])
Name = Annotated[str, Field(pattern=NAME)]
ARROW_TYPES = {
    "BOOLEAN": pa.bool_(),
    "INTEGER": pa.int32(),
    "BIGINT": pa.int64(),
    "FLOAT": pa.float32(),
    "DOUBLE": pa.float64(),
    "DATE": pa.date32(),
    "TIMESTAMP": pa.timestamp("us"),
    "TIMESTAMPTZ": pa.timestamp("us", tz="UTC"),
    "VARCHAR": pa.string(),
    "BLOB": pa.binary(),
}


class CatalogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    comment: str | None = Field(default=None, max_length=1000)


class SchemaCreate(CatalogCreate):
    pass


class ColumnSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_ ]*$")
    type: Literal[
        "BOOLEAN",
        "INTEGER",
        "BIGINT",
        "FLOAT",
        "DOUBLE",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "VARCHAR",
        "BLOB",
    ]


class PartitionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=128)
    transform: Literal["identity", "year", "month", "day", "hour"] = "identity"


class TableCreate(CatalogCreate):
    columns: list[ColumnSpec] = Field(min_length=1, max_length=2000)
    partitioning: list[PartitionSpec] = Field(default_factory=list, max_length=8)


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment: str | None = Field(default=None, max_length=1000)
    owner: str | None = Field(default=None, pattern=r"^(user|group|service):.{1,200}$")


class GrantChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: str = Field(pattern=r"^(user|group|service):.{1,200}$")
    add: list[str] = Field(default_factory=list, max_length=20)
    remove: list[str] = Field(default_factory=list, max_length=20)


class GrantsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: list[GrantChange] = Field(min_length=1, max_length=100)


def etag(response: Response, record: dict[str, Any]) -> None:
    response.headers["ETag"] = f'W/"{record["version"]}"'


def if_match(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip().removeprefix("W/").strip('"'))
    except ValueError:
        raise InvalidRequestError("Malformed If-Match header") from None


def public(kind: str, record: dict[str, Any], tables_view: bool = False) -> dict[str, Any]:
    if kind == "catalog":
        full = record["name"]
    elif kind == "schema":
        full = f"{record['catalog_name']}.{record['name']}"
    else:
        full = f"{record['catalog_name']}.{record['schema_name']}.{record['name']}"
    value = {
        "name": record["name"],
        "full_name": full,
        "owner": record["owner"],
        "comment": record.get("comment"),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }
    if kind != "catalog":
        value["catalog_name"] = record["catalog_name"]
    if kind == "view":
        value.update(
            schema_name=record["schema_name"], sql=record["sql"], columns=record["columns"]
        )
    if kind == "table":
        value.update(
            schema_name=record["schema_name"],
            format=record["format"],
            columns=record["columns"],
            partitioning=record["partitioning"],
            row_count=record["row_count"],
            size_bytes=record["size_bytes"],
            file_count=record["file_count"],
            snapshot_id=record["snapshot_id"],
            parent_table=record.get("parent_table_id"),
            materialized=record.get("materialized_info"),
        )
    return value


def freshness(svc: Container, table: dict[str, Any]) -> dict[str, Any] | None:
    """What a reader may know about a materialized view: when it was computed and if it is current.
    Staleness is a catalog comparison (source snapshots now vs at the last refresh): no data is read."""
    info = table.get("materialized")
    if not info:
        return None
    sources = info.get("sources") or {}
    keys = {name: tuple(name.split(".")) for name in sources}
    current = svc.store.tables_by_names(list(keys.values()))
    stale = not info.get("refreshed_at") or any(
        keys[name] not in current
        or str(current[keys[name]]["snapshot_id"] or "") != str(snap or "")
        for name, snap in sources.items()
    )
    return {
        "sql": info["sql"],
        "status": info["status"],
        "refreshed_at": info.get("refreshed_at"),
        "stale": stale,
        "sources": sorted(sources),
        "error": info.get("error"),
        "incremental": bool(info.get("incremental")),
        "refresh_every_minutes": info.get("refresh_every_minutes"),
    }


def table_public(svc: Container, record: dict[str, Any]) -> dict[str, Any]:
    return public("table", {**record, "materialized_info": freshness(svc, record)})


def visible_table(svc: Container, caller: Principal, catalog: str, schema: str, name: str) -> dict:
    record = svc.store.table(catalog, schema, name)
    if not svc.store.can_browse(caller, "table", record):
        raise not_found(f"Table {catalog}.{schema}.{name}")
    return record


def visible_view(svc: Container, caller: Principal, catalog: str, schema: str, name: str) -> dict:
    record = svc.store.view(catalog, schema, name)
    if not svc.store.can_browse(caller, "view", record):
        raise not_found(f"View {catalog}.{schema}.{name}")
    return record


def visible_schema(svc: Container, caller: Principal, catalog: str, name: str) -> dict:
    record = svc.store.schema(catalog, name)
    if not svc.store.can_browse(caller, "schema", record):
        raise not_found(f"Schema {catalog}.{name}")
    return record


def visible_catalog(svc: Container, caller: Principal, name: str) -> dict:
    record = svc.store.catalog(name)
    if not svc.store.can_browse(caller, "catalog", record):
        raise not_found(f"Catalog {name}")
    return record


def audit(
    svc: Container, request: Request, caller: Principal, action: str, securable: str, **detail
):
    svc.store.audit(caller, action, securable, "success", request.state.request_id, **detail)


# ------------------------------------------------------------------ catalogs
@router.get("/catalogs")
def list_catalogs(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    limit = page_size(page_size_)
    rows = svc.store.list_catalogs(decode_token(page_token), limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    items = [public("catalog", r) for r in rows if svc.store.can_browse(caller, "catalog", r)]
    return {"catalogs": items, "next_page_token": encode_token(rows[-1]["name"]) if more else None}


@router.post("/catalogs", status_code=201)
def create_catalog(
    body: CatalogCreate, svc: Svc, caller: Caller, request: Request, response: Response
):
    if not caller.is_admin:
        raise forbidden("Only metastore admins can create catalogs")
    record = svc.store.create_catalog(body.name, caller.id, body.comment)
    audit(svc, request, caller, "catalog.create", body.name)
    response.headers["Location"] = f"/api/v1/catalogs/{body.name}"
    etag(response, record)
    return public("catalog", record)


@router.get("/catalogs/{catalog}")
def get_catalog(catalog: str, svc: Svc, caller: Caller, response: Response):
    record = visible_catalog(svc, caller, catalog)
    etag(response, record)
    return public("catalog", record)


@router.patch("/catalogs/{catalog}")
def update_catalog(
    catalog: str,
    body: Update,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_catalog(svc, caller, catalog)
    owns(svc, caller, "catalog", record)
    svc.store.update_securable(
        "catalog", record, body.model_dump(exclude_none=True), if_match(match)
    )
    audit(svc, request, caller, "catalog.update", catalog, **body.model_dump(exclude_none=True))
    return public("catalog", svc.store.catalog(catalog))


@router.delete("/catalogs/{catalog}", status_code=204)
def delete_catalog(
    catalog: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_catalog(svc, caller, catalog)
    owns(svc, caller, "catalog", record)
    svc.store.delete_securable("catalog", record, if_match(match))
    audit(svc, request, caller, "catalog.delete", catalog)


# ------------------------------------------------------------------ schemas
@router.get("/catalogs/{catalog}/schemas")
def list_schemas(
    catalog: str,
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    parent = visible_catalog(svc, caller, catalog)
    limit = page_size(page_size_)
    rows = svc.store.list_schemas(parent, decode_token(page_token), limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        row.update(catalog_owner=parent["owner"])
        if svc.store.can_browse(caller, "schema", row):
            items.append(public("schema", row))
    return {"schemas": items, "next_page_token": encode_token(rows[-1]["name"]) if more else None}


@router.post("/catalogs/{catalog}/schemas", status_code=201)
def create_schema(
    catalog: str, body: SchemaCreate, svc: Svc, caller: Caller, request: Request, response: Response
):
    parent = visible_catalog(svc, caller, catalog)
    svc.store.require(caller, "catalog", parent, "CREATE_SCHEMA")
    record = svc.store.create_schema(parent, body.name, caller.id, body.comment)
    audit(svc, request, caller, "schema.create", f"{catalog}.{body.name}")
    response.headers["Location"] = f"/api/v1/catalogs/{catalog}/schemas/{body.name}"
    return public("schema", {**record, "catalog_name": catalog})


@router.get("/catalogs/{catalog}/schemas/{schema}")
def get_schema(catalog: str, schema: str, svc: Svc, caller: Caller, response: Response):
    record = visible_schema(svc, caller, catalog, schema)
    etag(response, record)
    return public("schema", record)


@router.patch("/catalogs/{catalog}/schemas/{schema}")
def update_schema(
    catalog: str,
    schema: str,
    body: Update,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_schema(svc, caller, catalog, schema)
    owns(svc, caller, "schema", record)
    svc.store.update_securable(
        "schema", record, body.model_dump(exclude_none=True), if_match(match)
    )
    audit(svc, request, caller, "schema.update", f"{catalog}.{schema}")
    return public("schema", svc.store.schema(catalog, schema))


@router.delete("/catalogs/{catalog}/schemas/{schema}", status_code=204)
def delete_schema(
    catalog: str,
    schema: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_schema(svc, caller, catalog, schema)
    owns(svc, caller, "schema", record)
    svc.store.delete_securable("schema", record, if_match(match))
    audit(svc, request, caller, "schema.delete", f"{catalog}.{schema}")


# ------------------------------------------------------------------ tables
@router.get("/catalogs/{catalog}/schemas/{schema}/tables")
def list_tables(
    catalog: str,
    schema: str,
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    parent = visible_schema(svc, caller, catalog, schema)
    limit = page_size(page_size_)
    rows = svc.store.list_tables(parent, decode_token(page_token), limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    items = [table_public(svc, r) for r in rows if svc.store.can_browse(caller, "table", r)]
    return {"tables": items, "next_page_token": encode_token(rows[-1]["name"]) if more else None}


@router.post("/catalogs/{catalog}/schemas/{schema}/tables", status_code=201)
async def create_table(
    catalog: str,
    schema: str,
    body: TableCreate,
    svc: Svc,
    caller: Caller,
    request: Request,
    response: Response,
):
    parent = visible_schema(svc, caller, catalog, schema)
    svc.store.require(caller, "schema", parent, "CREATE_TABLE")
    arrow = pa.schema([(c.name, ARROW_TYPES[c.type]) for c in body.columns])
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
    audit(svc, request, caller, "table.create", record["full_name"])
    response.headers["Location"] = f"/api/v1/catalogs/{catalog}/schemas/{schema}/tables/{body.name}"
    return record


def create_managed_table(
    svc: Container,
    schema: dict[str, Any],
    name: str,
    owner: str,
    comment: str | None,
    arrow: pa.Schema,
    partitioning: list[dict[str, str]],
    parent_table_id: str | None,
) -> dict[str, Any]:
    """Catalog row first (claims the name), then Iceberg metadata; undo the row on failure."""
    columns = {f.name.lower() for f in arrow}
    for item in partitioning:
        if item["column"].lower() not in columns:
            raise InvalidRequestError(f"Partition column {item['column']} is not in the table")
    record = svc.store.create_table(
        schema,
        {
            "name": name,
            "owner": owner,
            "comment": comment,
            "partitioning": partitioning,
            "parent_table_id": parent_table_id,
        },
    )
    record = svc.store.table_by_id(record["id"])
    try:
        table = svc.get(table_service).create(record, arrow, partitioning)
    except BaseException:
        svc.store.delete_securable("table", record, None)
        raise
    described = svc.get(table_service).describe(table)
    record = svc.store.update_table_state(
        record["id"], {"columns": described["columns"], "partitioning": described["partitioning"]}
    )
    return public("table", record)


@router.get("/catalogs/{catalog}/schemas/{schema}/tables/{table}")
def get_table(catalog: str, schema: str, table: str, svc: Svc, caller: Caller, response: Response):
    record = visible_table(svc, caller, catalog, schema, table)
    etag(response, record)
    return table_public(svc, record)


@router.patch("/catalogs/{catalog}/schemas/{schema}/tables/{table}")
def update_table(
    catalog: str,
    schema: str,
    table: str,
    body: Update,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_table(svc, caller, catalog, schema, table)
    owns(svc, caller, "table", record)
    svc.store.update_securable("table", record, body.model_dump(exclude_none=True), if_match(match))
    audit(svc, request, caller, "table.update", f"{catalog}.{schema}.{table}")
    return table_public(svc, svc.store.table(catalog, schema, table))


@router.delete("/catalogs/{catalog}/schemas/{schema}/tables/{table}", status_code=204)
async def delete_table(
    catalog: str,
    schema: str,
    table: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_table(svc, caller, catalog, schema, table)
    owns(svc, caller, "table", record)
    svc.store.delete_securable("table", record, if_match(match))
    await run_in_threadpool(svc.get(table_service).drop, record)
    audit(svc, request, caller, "table.delete", f"{catalog}.{schema}.{table}")


def owns(svc: Container, caller: Principal, kind: str, record: dict[str, Any]) -> None:
    if ALL not in svc.store.privileges(caller, svc.store.chain(kind, record)):
        raise forbidden(f"Only the {kind}'s owner or an admin can change it")


# ------------------------------------------------------------------ grants
SECURABLES = {"catalog", "schema", "table", "view", "volume", "model", "compute"}


def resolve(svc: Container, caller: Principal, kind: str, full_name: str) -> tuple[str, dict]:
    parts = full_name.split(".")
    if kind == "catalog" and len(parts) == 1:
        return kind, visible_catalog(svc, caller, parts[0])
    if kind == "schema" and len(parts) == 2:
        return kind, visible_schema(svc, caller, *parts)
    if kind == "table" and len(parts) == 3:
        return kind, visible_table(svc, caller, *parts)
    if kind == "view" and len(parts) == 3:
        return kind, visible_view(svc, caller, *parts)
    if kind in {"volume", "model"} and len(parts) == 3:
        from loamz.server.data.features.catalog.assets import visible

        return kind, visible(svc, caller, kind, *parts)
    raise InvalidRequestError(f"{full_name!r} is not a {kind} name")


@router.get("/grants/{securable_type}/{full_name}")
def get_grants(securable_type: str, full_name: str, svc: Svc, caller: Caller):
    if securable_type == "compute":
        return {"privilege_assignments": group(svc.store.grants("compute", full_name))}
    kind, record = resolve(svc, caller, securable_type, full_name)
    return {"privilege_assignments": group(svc.store.grants(kind, record["id"]))}


def group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_principal: dict[str, list[str]] = {}
    for row in rows:
        by_principal.setdefault(row["principal"], []).append(row["privilege"])
    return [{"principal": p, "privileges": sorted(v)} for p, v in sorted(by_principal.items())]


@router.patch("/grants/{securable_type}/{full_name}")
def update_grants(
    securable_type: str,
    full_name: str,
    body: GrantsUpdate,
    svc: Svc,
    caller: Caller,
    request: Request,
):
    if securable_type not in SECURABLES:
        raise InvalidRequestError("Unknown securable type")
    if securable_type == "compute":
        if not caller.is_admin:
            raise forbidden("Only admins manage compute grants")
        kind, securable_id = "compute", full_name
    else:
        kind, record = resolve(svc, caller, securable_type, full_name)
        owns(svc, caller, kind, record)
        securable_id = record["id"]
    for change in body.changes:
        for privilege in change.add:
            if privilege != ALL and privilege not in PRIVILEGES[kind]:
                raise InvalidRequestError(f"{privilege} does not apply to a {kind}")
    for change in body.changes:
        for privilege in change.add:
            svc.store.grant(kind, securable_id, change.principal, privilege, caller.id)
        for privilege in change.remove:
            svc.store.revoke(kind, securable_id, change.principal, privilege)
    audit(
        svc,
        request,
        caller,
        "grants.update",
        f"{kind}:{full_name}",
        changes=body.model_dump()["changes"],
    )
    return {"privilege_assignments": group(svc.store.grants(kind, securable_id))}


__all__ = ["router", "create_managed_table", "visible_table", "public", "duckdb_type"]


# ------------------------------------------------------------------ search
@router.get("/search")
def search(
    svc: Svc,
    caller: Caller,
    q: str = Query("", max_length=200),
    limit: int = Query(25, ge=1, le=100),
):
    """Tables and views the caller may read that match a word in the name, comment or a column."""
    results = []
    for kind, record in svc.store.search(q.strip(), 500):
        if not svc.store.can_browse(caller, kind, record):
            continue
        entry = public(kind, record)
        entry.update(
            kind=kind,
            columns=[c["name"] for c in record["columns"]],
            **({"row_count": record["row_count"]} if kind == "table" else {}),
        )
        results.append(entry)
        if len(results) >= limit:
            break
    return {"results": results}
