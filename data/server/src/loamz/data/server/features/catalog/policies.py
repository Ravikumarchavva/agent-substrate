"""/api/v1 table access policies: row filters and column masks.

Set by the table's owner (or an admin). They apply to every reader except the principals listed in
`exempt`, including readers who reach the table through a view (the view's definer is judged by
their own standing). See features/sql/policy.py for the expression language and how the
planner enforces them.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from loamz.data.server.features.catalog.api import Caller, Svc, audit, owns, visible_table
from loamz.data.server.features.sql.policy import validate_policy
from loamz.data.server.kernel.errors import invalid, not_found

router = APIRouter(prefix="/api/v1", tags=["policies"])
PRINCIPAL = r"^(user|group|service):.{1,200}$"


class ColumnMask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=128)
    mask: Literal["null", "redact", "hash", "expression"]
    expression: str | None = Field(default=None, max_length=2000)


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_filter: str | None = Field(default=None, max_length=2000)
    column_masks: list[ColumnMask] = Field(default_factory=list, max_length=500)
    exempt: list[str] = Field(default_factory=list, max_length=200)


def public(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_filter": policy["row_filter"],
        "column_masks": policy["column_masks"],
        "exempt": policy["exempt"],
        "updated_by": policy["updated_by"],
        "updated_at": policy["updated_at"],
        "version": policy["version"],
    }


def target(svc: Svc, caller: Caller, catalog: str, schema: str, table: str) -> dict[str, Any]:
    record = visible_table(svc, caller, catalog, schema, table)
    owns(svc, caller, "table", record)  # only the owner or an admin sees or changes policy
    return record


@router.put("/catalogs/{catalog}/schemas/{schema}/tables/{table}/policy")
async def put_policy(
    catalog: str,
    schema: str,
    table: str,
    body: PolicyBody,
    svc: Svc,
    caller: Caller,
    request: Request,
):
    record = target(svc, caller, catalog, schema, table)
    for principal in body.exempt:
        if not re.match(PRINCIPAL, principal):
            raise invalid(f"Exempt principals look like user:name or group:name, not {principal!r}")
    policy = body.model_dump()
    validate_policy(policy, record["columns"], svc.settings.mask_salt.get_secret_value())
    saved = await run_in_threadpool(svc.store.set_policy, record["id"], policy, caller.id)
    audit(
        svc,
        request,
        caller,
        "table.policy.set",
        f"{catalog}.{schema}.{table}",
        row_filter=body.row_filter,
        masked=[m.column for m in body.column_masks],
        exempt=body.exempt,
    )
    return public(saved)


@router.get("/catalogs/{catalog}/schemas/{schema}/tables/{table}/policy")
def get_policy(catalog: str, schema: str, table: str, svc: Svc, caller: Caller):
    record = target(svc, caller, catalog, schema, table)
    policy = svc.store.policy(record["id"])
    if policy is None:
        raise not_found("Policy")
    return public(policy)


@router.delete("/catalogs/{catalog}/schemas/{schema}/tables/{table}/policy", status_code=204)
def delete_policy(
    catalog: str, schema: str, table: str, svc: Svc, caller: Caller, request: Request
) -> Response:
    record = target(svc, caller, catalog, schema, table)
    if not svc.store.delete_policy(record["id"]):
        raise not_found("Policy")
    audit(svc, request, caller, "table.policy.delete", f"{catalog}.{schema}.{table}")
    return Response(status_code=204)
