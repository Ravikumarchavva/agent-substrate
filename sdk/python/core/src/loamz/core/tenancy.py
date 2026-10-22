"""Tenancy: the rules every Loamz service agrees on, in one place.

A tenant is a Keycloak organization, identified by its immutable id (the `tenant_id` a verified token carries).
Everything else is derived here, never re-invented by a caller:

- its **alias** (`tenant_alias`), a lower-case name safe to use in catalog, volume and database names;
- the **names** of what Loamz Data provisions for it: catalog `tenant_<alias>`, schemas `workspace` and `knowledge`,
  volume `tenant_<alias>.workspace.files`, database `tenant_<alias>`;
- the **group** its members are granted through (`org:<tenant_id>`), derived by the server from the verified token,
  never taken from a token's own groups;
- the **roles** a platform service account holds to provision and erase tenants;
- the **scopes** a delegated (agent) token may carry;
- the **keys** agent code uses for files (`tenants/<tenant>/<path>`) and how they split.
"""

from __future__ import annotations

import re
from typing import Final

TENANT_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
ALIAS_PATTERN: Final = r"^[a-z][a-z0-9_]{0,55}$"

# Groups (Keycloak groups claim) with a fixed meaning.
TENANT_ADMIN_GROUP: Final = "tenant-admins"
DATA_ADMIN_GROUP: Final = "data-admins"

# Client roles the platform's `cell-admin` service account holds on both servers.
PROVISION_TENANT_ROLE: Final = "provision-tenant"
ERASE_TENANT_ROLE: Final = "erase-tenant"

# What a delegated token may be scoped to. A route that declares none of these is closed to delegated tokens.
SCOPES: Final = (
    "data:read",
    "files:read",
    "files:write",
    "documents:read",
    "documents:write",
    "memory:read",
    "memory:write",
    "databases:read",
    "databases:connect",
)

WORKSPACE_SCHEMA: Final = "workspace"
KNOWLEDGE_SCHEMA: Final = "knowledge"
FILES_VOLUME: Final = "files"

KEY_PREFIX: Final = "tenants/"

_TENANT_ID = re.compile(TENANT_ID_PATTERN)


class TenancyError(ValueError):
    """A tenant id, key or prefix that breaks the rules above."""


def check_tenant_id(tenant_id: str) -> str:
    if not _TENANT_ID.match(tenant_id):
        raise TenancyError(f"invalid tenant id {tenant_id!r}")
    return tenant_id


def tenant_alias(tenant_id: str) -> str:
    """The tenant's alias: lower-case letters, digits and `_`, starting with a letter, at most 56 characters.

    `acme-corp` becomes `acme_corp`; a UUID becomes `t<uuid with underscores>`. Deterministic, so every service
    derives the same names without asking anyone.
    """
    alias = re.sub(r"[^a-z0-9]+", "_", check_tenant_id(tenant_id).lower()).strip("_")
    if not alias or not alias[0].isalpha():
        alias = "t" + alias
    return alias[:56]


def catalog_name(tenant_id: str) -> str:
    return f"tenant_{tenant_alias(tenant_id)}"


def files_volume(tenant_id: str) -> str:
    """The tenant's workspace volume: `tenant_<alias>.workspace.files`."""
    return f"{catalog_name(tenant_id)}.{WORKSPACE_SCHEMA}.{FILES_VOLUME}"


def database_name(tenant_id: str) -> str:
    """The tenant's managed Postgres database (Loamz Data `databases`)."""
    return catalog_name(tenant_id)


def catalog_marker(tenant_id: str) -> str:
    """Recorded on the tenant's catalog so it can be found by tenant id."""
    return f"tenant:{check_tenant_id(tenant_id)}"


def org_group(tenant_id: str) -> str:
    """The group every member of the tenant is in, derived from the verified token's tenant."""
    return f"org:{check_tenant_id(tenant_id)}"


def _check_segments(value: str, original: str) -> None:
    if "\x00" in value or "\\" in value or any(ord(c) < 32 for c in value):
        raise TenancyError(f"invalid key {original!r}: control characters or backslashes")
    if any(part in {".", ".."} for part in value.split("/")):
        raise TenancyError(f"invalid key {original!r}: '.' and '..' segments are not allowed")


def tenant_key(tenant_id: str, path: str) -> str:
    """`tenants/<tenant>/<path>`."""
    key = f"{KEY_PREFIX}{check_tenant_id(tenant_id)}/{path.lstrip('/')}"
    _check_segments(path, key)
    return key


def split_key(key: str) -> tuple[str, str]:
    """`tenants/t/users/u/a` -> `("t", "users/u/a")`. Refuses anything that is not a tenant key or climbs out."""
    if not key.startswith(KEY_PREFIX):
        raise TenancyError(f"invalid key {key!r}: keys start with '{KEY_PREFIX}<tenant>/'")
    tenant, _, path = key[len(KEY_PREFIX) :].partition("/")
    if not tenant or tenant in {".", ".."}:
        raise TenancyError(f"invalid key {key!r}: no tenant")
    _check_segments(path, key)
    return tenant, path


def split_prefix(prefix: str) -> tuple[str, str]:
    """Like `split_key` for a prefix, which may stop at the tenant (`tenants/t` or `tenants/t/`)."""
    if not prefix.startswith(KEY_PREFIX):
        raise TenancyError(
            f"invalid prefix {prefix!r}: prefixes start with '{KEY_PREFIX}<tenant>/'"
        )
    tenant, _, rest = prefix[len(KEY_PREFIX) :].partition("/")
    if not tenant or tenant in {".", ".."}:
        raise TenancyError(f"invalid prefix {prefix!r}: no tenant")
    _check_segments(rest, prefix)
    return tenant, rest
