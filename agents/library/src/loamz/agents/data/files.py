"""`DataFileStore`: the `FileStore` contract over Loamz Data's Files domain.

The agent host holds no storage credentials. Bytes go straight between this process and object storage
on URLs the data service signs; the data service owns paths, versions, quotas, ownership and audit.
"""

from __future__ import annotations

from loamz.agents.data._connection import DataServiceConnection, TokenProvider, mint
from loamz.agents.kernel.ports.files import FileStore
from loamz.data.client.errors import NotFoundError as ClientNotFoundError
from loamz.data.client.files import locate
from loamz.data.storage.errors import NotFoundError
from loamz.identity import tenancy

DEFAULT_CONTENT_TYPE = "application/octet-stream"


class DataFileStore(FileStore):
    """Keyed byte store backed by Loamz Data files volume."""

    def __init__(
        self,
        connection: DataServiceConnection,
        tokens: TokenProvider,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._connection = connection
        self._tokens = tokens
        self._tenant_id = tenant_id
        self._user_id = user_id

    async def _token(self, key_or_prefix: str) -> str:
        tenant = self._tenant_id
        user = self._user_id
        if tenant is None and key_or_prefix.startswith("tenants/"):
            try:
                tenant, path = tenancy.split_key(key_or_prefix)
                if path.startswith("users/"):
                    user = path.split("/")[1]
            except ValueError:
                tenant = key_or_prefix.removeprefix("tenants/").split("/")[0]
        return await mint(self._tokens, tenant or "default", user)

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = DEFAULT_CONTENT_TYPE,
    ) -> None:
        token = await self._token(key)
        volume, path = locate(key)
        with self._connection.bearer(token):
            await self._connection.client.files.upload(
                volume, path, data, content_type=content_type
            )

    async def download(self, key: str) -> bytes:
        token = await self._token(key)
        volume, path = locate(key)
        with self._connection.bearer(token):
            try:
                return await self._connection.client.files.read(volume, path)
            except ClientNotFoundError as exc:
                raise NotFoundError(f"File not found: {key}") from exc

    async def exists(self, key: str) -> bool:
        token = await self._token(key)
        volume, path = locate(key)
        with self._connection.bearer(token):
            return await self._connection.client.files.exists(volume, path)

    async def delete(self, key: str) -> None:
        token = await self._token(key)
        volume, path = locate(key)
        with self._connection.bearer(token):
            await self._connection.client.files.delete(volume, path, missing_ok=True)

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        if not prefix:
            return []
        token = await self._token(prefix)
        tenant = prefix.removeprefix("tenants/").split("/")[0]
        remainder = prefix[len(f"tenants/{tenant}/") :] if prefix.startswith("tenants/") else ""
        volume = tenancy.files_volume(tenant)
        items: list[tuple[str, int, float]] = []
        with self._connection.bearer(token):
            async for file_info in self._connection.client.files.list(volume, remainder):
                full_key = f"tenants/{tenant}/{file_info.path}"
                mtime = file_info.updated_at.timestamp() if file_info.updated_at else 0.0
                items.append((full_key, file_info.size, mtime))
        return items

    async def delete_prefix(self, prefix: str) -> int:
        if not prefix:
            return 0
        token = await self._token(prefix)
        tenant = prefix.removeprefix("tenants/").split("/")[0]
        remainder = prefix[len(f"tenants/{tenant}/") :] if prefix.startswith("tenants/") else ""
        volume = tenancy.files_volume(tenant)
        with self._connection.bearer(token):
            res = await self._connection.client.files.delete_prefix(volume, remainder)
            return int(res.get("deleted", 0))

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        token = await self._token(source_prefix)
        s_tenant = source_prefix.removeprefix("tenants/").split("/")[0]
        d_tenant = dest_prefix.removeprefix("tenants/").split("/")[0]
        if s_tenant != d_tenant:
            raise ValueError("cannot copy across tenants")
        s_rel = source_prefix[len(f"tenants/{s_tenant}/") :]
        d_rel = dest_prefix[len(f"tenants/{d_tenant}/") :]
        volume = tenancy.files_volume(s_tenant)
        with self._connection.bearer(token):
            res = await self._connection.client.files.copy_prefix(volume, s_rel, d_rel)
            return int(res.get("copied", 0))
