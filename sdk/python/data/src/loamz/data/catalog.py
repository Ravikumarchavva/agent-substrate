"""`client.catalog` (and `client.tenants`): browse and manage the governed namespace.

The methods are written once; on `AsyncDataClient` the same calls are awaited.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loamz.core import tenancy
from loamz.data._http import AsyncCore, SyncCore


def _key(name: str) -> Callable[[Any], Any]:
    return lambda body: body[name]


def _split(full_name: str, parts: int) -> list[str]:
    names = full_name.split(".")
    if len(names) != parts:
        raise ValueError(f"{full_name!r} is not a {parts}-part name")
    return names


class _CatalogCalls:
    def _do(self, method: str, path: str, post: Callable | None = None, **kwargs: Any):
        raise NotImplementedError

    def catalogs(self):
        return self._do("GET", "/catalogs", _key("catalogs"))

    def create_catalog(self, name: str, comment: str | None = None):
        return self._do("POST", "/catalogs", json={"name": name, "comment": comment})

    def schemas(self, catalog: str):
        return self._do("GET", f"/catalogs/{catalog}/schemas", _key("schemas"))

    def create_schema(self, catalog: str, name: str, comment: str | None = None):
        return self._do(
            "POST", f"/catalogs/{catalog}/schemas", json={"name": name, "comment": comment}
        )

    def tables(self, catalog: str, schema: str):
        return self._do("GET", f"/catalogs/{catalog}/schemas/{schema}/tables", _key("tables"))

    def table(self, full_name: str):
        catalog, schema, name = _split(full_name, 3)
        return self._do("GET", f"/catalogs/{catalog}/schemas/{schema}/tables/{name}")

    def views(self, catalog: str, schema: str):
        return self._do("GET", f"/catalogs/{catalog}/schemas/{schema}/views", _key("views"))

    def view(self, full_name: str):
        catalog, schema, name = _split(full_name, 3)
        return self._do("GET", f"/catalogs/{catalog}/schemas/{schema}/views/{name}")

    def create_view(self, full_name: str, sql: str, comment: str | None = None):
        """Define a named query. Readers need SELECT on the view, not on the tables it reads."""
        catalog, schema, name = _split(full_name, 3)
        return self._do(
            "POST",
            f"/catalogs/{catalog}/schemas/{schema}/views",
            json={"name": name, "sql": sql, "comment": comment},
        )

    def volumes(self, catalog: str, schema: str):
        return self._do("GET", f"/catalogs/{catalog}/schemas/{schema}/volumes", _key("volumes"))

    def create_volume(self, full_name: str, comment: str | None = None):
        catalog, schema, name = _split(full_name, 3)
        return self._do(
            "POST",
            f"/catalogs/{catalog}/schemas/{schema}/volumes",
            json={"name": name, "comment": comment},
        )

    def search(self, text: str = "", *, limit: int = 25):
        """Tables and views you may read whose name, comment or a column matches `text`."""
        return self._do("GET", "/search", _key("results"), params={"q": text, "limit": limit})

    def grants(self, securable_type: str, full_name: str):
        return self._do(
            "GET", f"/grants/{securable_type}/{full_name}", _key("privilege_assignments")
        )

    def update_grants(
        self,
        securable_type: str,
        full_name: str,
        principal: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ):
        return self._do(
            "PATCH",
            f"/grants/{securable_type}/{full_name}",
            json={"changes": [{"principal": principal, "add": add or [], "remove": remove or []}]},
        )


class Catalog(_CatalogCalls):
    def __init__(self, core: SyncCore):
        self._core = core

    def _do(self, method, path, post=None, **kwargs):
        body = self._core.call(method, path, **kwargs)
        return post(body) if post else body


class AsyncCatalog(_CatalogCalls):
    def __init__(self, core: AsyncCore):
        self._core = core

    async def _do(self, method, path, post=None, **kwargs):
        body = await self._core.call(method, path, **kwargs)
        return post(body) if post else body


class _TenantCalls:
    def _do(self, method: str, path: str, post: Callable | None = None, **kwargs: Any):
        raise NotImplementedError

    def ensure(self, tenant_id: str):
        """Create (once) the tenant's catalog `tenant_<alias>`, its `workspace` and `knowledge` schemas, the
        `workspace.files` volume and the grants for the tenant's group. The alias and names are derived from the
        id (`loamz.core.tenancy`), never chosen by the caller. Needs the `provision-tenant` role."""
        return self._do("POST", "/tenants", json={"tenant_id": tenancy.check_tenant_id(tenant_id)})


class Tenants(_TenantCalls):
    def __init__(self, core: SyncCore):
        self._core = core

    def _do(self, method, path, post=None, **kwargs):
        return self._core.call(method, path, **kwargs)


class AsyncTenants(_TenantCalls):
    def __init__(self, core: AsyncCore):
        self._core = core

    async def _do(self, method, path, post=None, **kwargs):
        return await self._core.call(method, path, **kwargs)
