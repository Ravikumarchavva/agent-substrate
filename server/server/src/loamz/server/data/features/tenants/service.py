"""Provisioning a tenant: an *ensure*. Calling it again creates nothing and changes nothing.

It is for the platform that onboards tenants, not for end users: a metastore admin, or a service account holding the
client role `provision-tenant` (`tenancy.PROVISION_TENANT_ROLE`, the platform's `cell-admin`). Delegated (agent)
tokens never can: the route declares no scope.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loamz.core import tenancy
from loamz.engine.errors import ConflictError, NotFoundError
from loamz.server.data.features.tenants.model import Tenant
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import forbidden
from loamz.server.data.kernel.metadata import CatalogStore
from loamz.server.data.kernel.principal import Principal
from loamz.server.data.kernel.settings import Settings

SCHEMAS = (tenancy.WORKSPACE_SCHEMA, tenancy.KNOWLEDGE_SCHEMA)


def _get_or_create(
    find: Callable[[], dict[str, Any]], create: Callable[[], dict[str, Any]]
) -> tuple[dict[str, Any], bool]:
    try:
        return find(), False
    except NotFoundError:
        pass
    try:
        return create(), True
    except ConflictError:  # another replica created it between the check and the insert
        return find(), False


class TenantsService:
    def __init__(self, store: CatalogStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    def ensure(self, caller: Principal, tenant_id: str, request_id: str | None = None) -> Tenant:
        if not (caller.is_admin or caller.has_role(tenancy.PROVISION_TENANT_ROLE)):
            raise forbidden(f"Provisioning tenants needs the {tenancy.PROVISION_TENANT_ROLE} role")
        tenant = self._provision(tenant_id, caller.id)
        if tenant.created:
            self._store.audit(
                caller, "tenant.ensure", tenant.catalog, "success", request_id, tenant_id=tenant_id
            )
        return tenant

    def _provision(self, tenant_id: str, granted_by: str) -> Tenant:
        store = self._store
        owner = f"group:{self._settings.admin_group}"
        name = tenancy.catalog_name(tenant_id)
        marker = tenancy.catalog_marker(tenant_id)
        catalog, created = _get_or_create(
            lambda: store.catalog(name), lambda: store.create_catalog(name, owner, marker)
        )
        if catalog["comment"] != marker:
            raise ConflictError(f"The catalog {name} already belongs to something else")
        grantee = f"group:{tenancy.org_group(tenant_id)}"
        store.grant("catalog", catalog["id"], grantee, "USE_CATALOG", granted_by)
        schemas = {}
        for schema_name in SCHEMAS:
            schemas[schema_name], new = _get_or_create(
                lambda s=schema_name: store.schema(name, s),
                lambda s=schema_name: store.create_schema(catalog, s, owner, None),
            )
            created |= new
            store.grant("schema", schemas[schema_name]["id"], grantee, "USE_SCHEMA", granted_by)
        volume, new = _get_or_create(
            lambda: store.asset("volume", name, tenancy.WORKSPACE_SCHEMA, tenancy.FILES_VOLUME),
            lambda: store.create_asset(
                "volume",
                schemas[tenancy.WORKSPACE_SCHEMA],
                {
                    "name": tenancy.FILES_VOLUME,
                    "owner": owner,
                    "comment": "Tenant files",
                    "properties": {"bucket": self._settings.volumes_bucket, "layout": "files/v1"},
                },
            ),
        )
        created |= new
        for privilege in ("READ_VOLUME", "WRITE_VOLUME"):
            store.grant("volume", volume["id"], grantee, privilege, granted_by)
        return Tenant(
            tenant_id=tenant_id,
            alias=tenancy.tenant_alias(tenant_id),
            catalog=name,
            schemas=[f"{name}.{s}" for s in SCHEMAS],
            volumes=[tenancy.files_volume(tenant_id)],
            group=grantee,
            created=created,
        )


def tenants(container: Container) -> TenantsService:
    """Provider."""
    return TenantsService(container.store, container.settings)
