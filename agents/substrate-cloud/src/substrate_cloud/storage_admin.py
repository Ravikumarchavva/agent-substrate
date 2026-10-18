"""What the admin storage page shows and changes: tenants' usage, conversation drill-down and quotas.

Locally that is the store's own ``Files`` folder (read-only here: a per-tenant quota is a volume setting of the data service now, and
nothing in this process keeps one). With a data service it is the Files API as an administrator: tenants are the catalogs
``POST /api/v1/tenants`` made, usage and quota are the volume's, and the platform signs in with an OIDC client of its own
(``DATA_ADMIN_*``), never with a user's token.
"""

from __future__ import annotations

from typing import Any, Protocol

from substrate_data_client import AsyncDataClient, DataError, NotFound


class StorageUnsupported(Exception):
    """This deployment cannot do that (status 501 to the caller)."""


class StorageAdmin(Protocol):
    async def tenants(self) -> list[dict[str, Any]]: ...
    async def conversations(self, tenant_id: str) -> list[tuple[str, int, int]]: ...
    async def set_quota(
        self, tenant_id: str, quota_bytes: int | None
    ) -> dict[str, Any]: ...


class LocalStorageAdmin:
    def __init__(self, files: Any) -> None:
        self._files = files

    async def tenants(self) -> list[dict[str, Any]]:
        result = []
        for tenant_id in await self._files.list_all_tenants():
            conversations = await self._files.list_conversations(tenant_id)
            result.append(
                {
                    "tenant_id": tenant_id,
                    "used_bytes": await self._files.usage_bytes(tenant_id),
                    "quota_bytes": self._files.effective_quota(tenant_id),
                    "conversation_count": len(conversations),
                }
            )
        return result

    async def conversations(self, tenant_id: str) -> list[tuple[str, int, int]]:
        return list(await self._files.list_conversations(tenant_id))

    async def set_quota(
        self, tenant_id: str, quota_bytes: int | None
    ) -> dict[str, Any]:
        raise StorageUnsupported(
            "A tenant's storage quota is set on the data service (DATA_SERVICE_URL); the local folder store has one global quota."
        )


class DataStorageAdmin:
    def __init__(self, client: AsyncDataClient, volume_of: Any) -> None:
        self._client = client
        self._volume_of = volume_of

    async def tenants(self) -> list[dict[str, Any]]:
        result = []
        for catalog in await self._client.catalog.catalogs():
            marker = str(catalog.get("comment") or "")
            if not (
                catalog["name"].startswith("tenant_") and marker.startswith("tenant:")
            ):
                continue
            tenant_id = marker.removeprefix("tenant:")
            volume = f"{catalog['name']}.workspace.files"
            try:
                usage = await self._client.files.usage(volume)
            except DataError:
                continue
            result.append(
                {
                    "tenant_id": tenant_id,
                    "used_bytes": usage.volume_bytes,
                    "quota_bytes": usage.quota_bytes,
                    "conversation_count": await self._conversation_count(volume),
                }
            )
        return result

    async def _conversation_count(self, volume: str) -> int:
        count = 0
        for user in (await self._client.files.list_dir(volume, "users/")).directories:
            listing = await self._client.files.list_dir(
                volume, f"{user.rstrip('/')}/conversations/"
            )
            count += len(listing.directories)
        return count

    async def conversations(self, tenant_id: str) -> list[tuple[str, int, int]]:
        sizes: dict[str, list[int]] = {}
        try:
            async for info in self._client.files.list(
                self._volume_of(tenant_id), "users/"
            ):
                parts = info.path.split("/")
                if len(parts) > 4 and parts[2] == "conversations":
                    totals = sizes.setdefault(parts[3], [0, 0])
                    totals[0] += info.size
                    totals[1] += 1
        except NotFound:
            return []
        return [(cid, size, count) for cid, (size, count) in sorted(sizes.items())]

    async def set_quota(
        self, tenant_id: str, quota_bytes: int | None
    ) -> dict[str, Any]:
        volume = self._volume_of(tenant_id)
        await self._client.files.set_quota(volume, quota_bytes)
        usage = await self._client.files.usage(volume)
        return {"tenant_id": tenant_id, "quota_bytes": usage.quota_bytes}
