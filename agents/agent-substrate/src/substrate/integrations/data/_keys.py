"""Where a ``FileStore`` key lives in substrate-data: which volume, which path.

A key is ``tenants/{tenant}/{path}`` (``substrate.workspace.layout``). The tenant picks the volume
(``tenant_<alias>.workspace.files``, the one ``POST /api/v1/tenants`` provisions); the rest is the path in it,
unchanged, so ``users/{user}/...`` is exactly the server's ownership rule. The mapping is data_client's
``FILESTORE_KEY_MAP.md``, in code.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from substrate.stores.files import WorkspacePathError

TENANT_PREFIX = "tenants/"
VolumeResolver = Callable[[str], str]


def tenant_alias(tenant_id: str) -> str:
    """The alias a tenant is provisioned under when nothing says otherwise: lower-case letters, digits and ``_``.

    ``acme-corp`` becomes ``acme_corp``. The provisioner (``client.tenants.ensure(tenant_id, alias)``) must use the same
    alias, or give the adapter an explicit ``volumes`` mapping.
    """
    alias = re.sub(r"[^a-z0-9]+", "_", tenant_id.lower()).strip("_")
    if not alias or not alias[0].isalpha():
        alias = "t" + alias
    return alias[:56]


def default_volume(tenant_id: str) -> str:
    return f"tenant_{tenant_alias(tenant_id)}.workspace.files"


def volume_resolver(
    volumes: Mapping[str, str] | VolumeResolver | None,
) -> VolumeResolver:
    if volumes is None:
        return default_volume
    if callable(volumes):
        return volumes
    mapping = dict(volumes)
    return lambda tenant_id: mapping.get(tenant_id) or default_volume(tenant_id)


def _check_segments(value: str, key: str) -> None:
    if "\x00" in value or "\\" in value or any(ord(c) < 32 for c in value):
        raise WorkspacePathError(
            f"invalid key {key!r}: control characters or backslashes"
        )
    if any(part in {".", ".."} for part in value.split("/")):
        raise WorkspacePathError(
            f"invalid key {key!r}: '.' and '..' segments are not allowed"
        )


def split_key(key: str) -> tuple[str, str]:
    """``tenants/t/users/u/a`` -> ``("t", "users/u/a")``. Refused before any call when it is not a tenant key or climbs out."""
    if not key.startswith(TENANT_PREFIX):
        raise WorkspacePathError(
            f"invalid key {key!r}: keys start with 'tenants/<tenant>/'"
        )
    tenant, _, path = key[len(TENANT_PREFIX) :].partition("/")
    if not tenant or tenant in {".", ".."}:
        raise WorkspacePathError(f"invalid key {key!r}: no tenant")
    _check_segments(path, key)
    return tenant, path


def split_prefix(prefix: str) -> tuple[str, str]:
    """Like ``split_key`` for a prefix, which may stop at the tenant (``tenants/t`` or ``tenants/t/``) and may end in ``/``."""
    if not prefix.startswith(TENANT_PREFIX):
        raise WorkspacePathError(
            f"invalid prefix {prefix!r}: prefixes start with 'tenants/<tenant>/'"
        )
    tenant, _, rest = prefix[len(TENANT_PREFIX) :].partition("/")
    if not tenant or tenant in {".", ".."}:
        raise WorkspacePathError(f"invalid prefix {prefix!r}: no tenant")
    _check_segments(rest, prefix)
    return tenant, rest
