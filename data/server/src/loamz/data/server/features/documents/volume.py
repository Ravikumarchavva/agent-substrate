"""The document bundles' file store: a tenant's volume, written by the control plane itself.

The `Library` engine keeps each collection as an OKF bundle (markdown files) under keys `tenants/{tenant}/{path}`
(`loamz.identity.tenancy.tenant_key`): the same key map the agents' data adapter uses, so the bundle sits in the tenant's `workspace.files` volume as ordinary
versioned, quota-counted files (`users/{sub}/…` stays that subject's) and an export or erasure of the volume reaches it.
The control plane does not parse anything: it stores and reads back what the engine hands it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from loamz.data.server.features.files.paths import normalize_path, normalize_prefix
from loamz.data.server.features.files.repo import FileCatalog
from loamz.data.server.features.files.service import FilesService
from loamz.data.server.kernel.errors import TenantNotProvisionedError
from loamz.data.storage.errors import InvalidRequestError, LoamzError, NotFoundError
from loamz.identity import tenancy

WRITER = "service:documents"
VOLUME = (tenancy.WORKSPACE_SCHEMA, tenancy.FILES_VOLUME)


def split_key(key: str) -> tuple[str, str]:
    """`tenants/t/users/u/a.md` -> `("t", "users/u/a.md")`; a key that is not a file of a tenant is refused."""
    try:
        tenant, path = tenancy.split_key(key)
    except tenancy.TenancyError as exc:
        raise InvalidRequestError(str(exc)) from None
    if not path:
        raise InvalidRequestError(f"invalid key {key!r}")
    return tenant, path


def tenant_volume(files: FilesService, tenant_id: str) -> dict[str, Any]:
    """The tenant's volume record (provisioned by `POST /api/v1/tenants`), found by the tenant id its catalog carries."""
    store = files.store
    with store.connection() as db:
        catalog = db.execute(
            "SELECT name FROM catalogs WHERE comment=?", (tenancy.catalog_marker(tenant_id),)
        ).fetchone()
    if catalog is None:
        raise TenantNotProvisionedError("The tenant has no workspace volume yet")
    return store.asset("volume", catalog["name"], *VOLUME)


class VolumeFileStore:
    """A `FileStore` over the volumes of every tenant (keys name the tenant), for the Library engine."""

    def __init__(self, files: FilesService) -> None:
        self.files = files
        self._volumes: dict[str, dict[str, Any]] = {}

    def volume(self, tenant_id: str) -> dict[str, Any]:
        found = self._volumes.get(tenant_id)
        if found is None:
            found = self._volumes[tenant_id] = tenant_volume(self.files, tenant_id)
        return found

    @property
    def catalog(self) -> FileCatalog:
        return self.files.catalog

    # ------------------------------------------------------------------ FileStore
    async def upload(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        tenant, path = split_key(key)
        await asyncio.to_thread(self._upload, tenant, normalize_path(path), data, content_type)

    def _upload(self, tenant: str, path: str, data: bytes, content_type: str) -> None:
        volume = self.volume(tenant)
        storage = self.files.storage
        object_key = storage.object_key(volume["id"])
        sha256 = storage.put_bytes(object_key, data, content_type)
        try:
            self.catalog.commit_version(
                volume["id"],
                path,
                {
                    "object_key": object_key,
                    "size": len(data),
                    "sha256": sha256,
                    "sha256_verified": True,
                    "content_type": content_type,
                },
                WRITER,
            )
        except LoamzError:
            storage.delete([object_key])
            raise

    async def download(self, key: str) -> bytes:
        tenant, path = split_key(key)
        return await asyncio.to_thread(self._download, key, tenant, normalize_path(path))

    def _download(self, key: str, tenant: str, path: str) -> bytes:
        volume = self.volume(tenant)
        try:
            obj = self.catalog.current_object(volume["id"], path)
            return self.files.storage.get_bytes(obj["object_key"])
        except NotFoundError:
            raise NotFoundError(f"Object not found: {key}") from None

    async def exists(self, key: str) -> bool:
        tenant, path = split_key(key)
        volume = await asyncio.to_thread(self.volume, tenant)
        return (
            await asyncio.to_thread(self.catalog.head, volume["id"], normalize_path(path))
            is not None
        )

    async def delete(self, key: str) -> None:
        tenant, path = split_key(key)
        volume = await asyncio.to_thread(self.volume, tenant)
        await asyncio.to_thread(self.catalog.soft_delete, volume["id"], normalize_path(path))

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        tenant, path = self._split_prefix(prefix)
        return await asyncio.to_thread(self._list, tenant, path)

    def _list(self, tenant: str, path: str) -> list[tuple[str, int, float]]:
        volume = self.volume(tenant)
        prefix = normalize_prefix(path)
        found: list[tuple[str, int, float]] = []
        after = ""
        while True:
            rows, _dirs, after_token = self.catalog.list_entries(
                volume["id"], prefix, None, after, 1000, lambda _p: True
            )
            for row in rows:
                updated = datetime.fromisoformat(row["updated_at"]).astimezone(UTC).timestamp()
                found.append((tenancy.tenant_key(tenant, row["path"]), int(row["size"]), updated))
            if not after_token:
                return found
            after = after_token

    async def delete_prefix(self, prefix: str) -> int:
        tenant, path = self._split_prefix(prefix)
        return await asyncio.to_thread(self._delete_prefix, tenant, path)

    def _delete_prefix(self, tenant: str, path: str) -> int:
        volume = self.volume(tenant)
        files, _bytes = self.catalog.delete_prefix(volume["id"], normalize_prefix(path))
        return files

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        tenant, source = self._split_prefix(source_prefix)
        other, dest = self._split_prefix(dest_prefix)
        if tenant != other:
            raise InvalidRequestError("a copy stays inside one tenant")
        volume = await asyncio.to_thread(self.volume, tenant)
        done = await asyncio.to_thread(
            self.catalog.copy_prefix,
            volume["id"],
            normalize_prefix(source),
            normalize_prefix(dest),
            WRITER,
        )
        return int(done["copied"])

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        tenant, path = split_key(key)
        volume = await asyncio.to_thread(self.volume, tenant)
        obj = await asyncio.to_thread(
            self.catalog.current_object, volume["id"], normalize_path(path)
        )
        return self.files.storage.sign_get(obj["object_key"], expires_in=expires_in)

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        volume = await asyncio.to_thread(self.volume, tenant_id)
        return await asyncio.to_thread(self.catalog.live_bytes, volume["id"])

    @staticmethod
    def _split_prefix(prefix: str) -> tuple[str, str]:
        try:
            return tenancy.split_prefix(prefix)
        except tenancy.TenancyError as exc:
            raise InvalidRequestError(str(exc)) from None
