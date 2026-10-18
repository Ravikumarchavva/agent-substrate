"""/api/v1 views: named, governed queries. Readers need SELECT on the view, not on its tables."""

from __future__ import annotations

from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from loamz.data.server.features.catalog.api import (
    audit,
    if_match,
    owns,
    public,
    visible_schema,
    visible_view,
)
from loamz.data.server.features.sql.api import StatementRequest, json_response, quoted, run_json
from loamz.data.server.kernel.http import Caller, Svc, decode_token, encode_token, page_size
from loamz.data.server.kernel.metadata import NAME
from loamz.data.server.kernel.principal import Principal

router = APIRouter(prefix="/api/v1", tags=["views"])


class ViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME)
    sql: str = Field(min_length=1, max_length=100_000)
    comment: str | None = Field(default=None, max_length=1000)


def definer_info(caller: Principal) -> dict[str, Any]:
    return {
        "subject": caller.subject,
        "groups": sorted(caller.groups),
        "is_admin": caller.is_admin,
    }


@router.post("/catalogs/{catalog}/schemas/{schema}/views", status_code=201)
async def create_view(
    catalog: str,
    schema: str,
    body: ViewCreate,
    svc: Svc,
    caller: Caller,
    request: Request,
    response: Response,
):
    parent = visible_schema(svc, caller, catalog, schema)
    svc.store.require(caller, "schema", parent, "CREATE_TABLE")
    definition = body.sql.strip().rstrip(";").strip()
    # Running the definition (LIMIT 0) as the creator validates the SQL, proves the creator may read
    # everything it touches, and yields the view's column names and types.
    payload, _ = await run_json(
        svc,
        caller,
        StatementRequest(
            sql=f"SELECT * FROM (\n{definition}\n) AS definition LIMIT 0",
            catalog=catalog,
            schema=schema,
        ),
        request,
        1,
    )
    columns = orjson.loads(payload)["columns"]
    record = svc.store.create_view(
        parent,
        {
            "name": body.name,
            "owner": caller.id,
            "comment": body.comment,
            "sql": definition,
            "columns": columns,
            "definer": definer_info(caller),
        },
    )
    audit(svc, request, caller, "view.create", f"{catalog}.{schema}.{body.name}")
    response.headers["Location"] = f"/api/v1/catalogs/{catalog}/schemas/{schema}/views/{body.name}"
    record = svc.store.view(catalog, schema, body.name) | {"id": record["id"]}
    return public("view", record)


@router.get("/catalogs/{catalog}/schemas/{schema}/views")
def list_views(
    catalog: str,
    schema: str,
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    parent = visible_schema(svc, caller, catalog, schema)
    limit = page_size(page_size_)
    rows = svc.store.list_views(parent, decode_token(page_token), limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    items = [public("view", r) for r in rows if svc.store.can_browse(caller, "view", r)]
    return {"views": items, "next_page_token": encode_token(rows[-1]["name"]) if more else None}


@router.get("/catalogs/{catalog}/schemas/{schema}/views/{view}")
def get_view(catalog: str, schema: str, view: str, svc: Svc, caller: Caller, response: Response):
    record = visible_view(svc, caller, catalog, schema, view)
    response.headers["ETag"] = f'W/"{record["version"]}"'
    return public("view", record)


@router.delete("/catalogs/{catalog}/schemas/{schema}/views/{view}", status_code=204)
def delete_view(
    catalog: str,
    schema: str,
    view: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    record = visible_view(svc, caller, catalog, schema, view)
    owns(svc, caller, "view", record)
    svc.store.delete_securable("view", record, if_match(match))
    audit(svc, request, caller, "view.delete", f"{catalog}.{schema}.{view}")


@router.get("/catalogs/{catalog}/schemas/{schema}/views/{view}/preview")
async def preview_view(
    catalog: str,
    schema: str,
    view: str,
    svc: Svc,
    caller: Caller,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=10_000_000),
    compute_id: str | None = None,
):
    visible_view(svc, caller, catalog, schema, view)
    sql = f"SELECT * FROM {quoted(catalog, schema, view)} LIMIT {limit} OFFSET {offset}"
    payload, meta = await run_json(
        svc, caller, StatementRequest(sql=sql, compute_id=compute_id), request, limit
    )
    meta.update(offset=offset)
    return json_response(payload, meta)
