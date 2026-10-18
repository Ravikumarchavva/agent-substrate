"""/api/v1/tenants: idempotent provisioning of a tenant's catalog, schemas, volume and grants.

`POST /tenants` is an *ensure*: calling it again with the same body creates nothing and changes
nothing. It is for the platform that onboards tenants (the control plane), not for end users.

Who may call it: a metastore admin, or a member of `SUBSTRATE_DATA_PROVISIONER_GROUP` (default
`data-provisioners`), or a service account holding the realm client role `provision-tenant` (the platform's
`cell-admin`).
Delegated (agent) tokens never can: the route declares no scope.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from substrate_data.api.routes.catalog import Caller, Svc
from substrate_data.api.services import Services
from substrate_data.core.errors import Problem, forbidden

router = APIRouter(prefix="/api/v1", tags=["tenants"])
SCHEMAS = ("workspace", "knowledge")
VOLUME = ("workspace", "files")


class TenantEnsure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    # Names the catalog: `tenant_<alias>`.
    alias: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,55}$")]


def get_or_create(find, create) -> tuple[dict[str, Any], bool]:
    try:
        return find(), False
    except Problem as exc:
        if exc.status != 404:
            raise
    try:
        return create(), True
    except Problem as exc:  # another replica created it between the check and the insert
        if exc.status != 409:
            raise
        return find(), False


def provision(svc: Services, body: TenantEnsure, granted_by: str) -> dict[str, Any]:
    owner = f"group:{svc.settings.admin_group}"
    store, created = svc.store, False
    name = f"tenant_{body.alias}"
    marker = f"tenant:{body.tenant_id}"
    catalog, new = get_or_create(
        lambda: store.catalog(name), lambda: store.create_catalog(name, owner, marker)
    )
    created |= new
    if catalog["comment"] != marker:
        raise Problem(
            409, "tenant-conflict", f"The catalog {name} already belongs to something else"
        )
    grantee = f"group:org:{body.tenant_id}"
    store.grant("catalog", catalog["id"], grantee, "USE_CATALOG", granted_by)
    schemas = {}
    for schema_name in SCHEMAS:
        schemas[schema_name], new = get_or_create(
            lambda s=schema_name: store.schema(name, s),
            lambda s=schema_name: store.create_schema(catalog, s, owner, None),
        )
        created |= new
        store.grant("schema", schemas[schema_name]["id"], grantee, "USE_SCHEMA", granted_by)
    schema_name, volume_name = VOLUME
    volume, new = get_or_create(
        lambda: store.asset("volume", name, schema_name, volume_name),
        lambda: store.create_asset(
            "volume",
            schemas[schema_name],
            {
                "name": volume_name,
                "owner": owner,
                "comment": "Tenant files",
                "properties": {"bucket": svc.settings.volumes_bucket, "layout": "files/v1"},
            },
        ),
    )
    created |= new
    for privilege in ("READ_VOLUME", "WRITE_VOLUME"):
        store.grant("volume", volume["id"], grantee, privilege, granted_by)
    return {
        "tenant_id": body.tenant_id,
        "alias": body.alias,
        "catalog": name,
        "schemas": [f"{name}.{s}" for s in SCHEMAS],
        "volumes": [f"{name}.{schema_name}.{volume_name}"],
        "group": grantee,
        "created": created,
    }


@router.post("/tenants")
def ensure_tenant(
    body: TenantEnsure, svc: Svc, caller: Caller, request: Request, response: Response
):
    if not (
        caller.is_admin
        or svc.settings.provisioner_group in caller.groups
        or "provision-tenant"
        in caller.roles  # the platform's cell-admin service account (realm client role)
    ):
        raise forbidden("Provisioning tenants needs the data:provisioner role")
    result = provision(svc, body, caller.id)
    if result["created"]:
        response.status_code = 201
        svc.store.audit(
            caller, "tenant.ensure", result["catalog"], "success", request.state.request_id,
            tenant_id=body.tenant_id,
        )  # fmt: skip
    return result
