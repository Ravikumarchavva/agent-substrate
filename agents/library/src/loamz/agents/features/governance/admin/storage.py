"""What the admin storage page shows: tenants' file usage and the conversation drill-down.

Read from the file store's keys (`tenants/<tenant>/users/<user>/conversations/<conversation>/...`), which any
`FileStore` can list. Usage is metered per tenant (the coarsest identity every key carries).

TODO(integration): with a data service, tenants are its catalogs and usage/quota are the volume's (the data service's
admin API, signed in as the platform); swap `StorageAdmin` for an adapter over `loamz.data.client`.
"""

from __future__ import annotations

from loamz.agents.kernel.ports.files import FileStore
from loamz.identity.tenancy import TenancyError, split_key


class StorageAdmin:
    def __init__(self, files: FileStore, quota_bytes: int | None) -> None:
        self._files = files
        self._quota = quota_bytes

    async def tenants(self, only: str | None = None) -> list[dict]:
        found: dict[str, dict] = {}
        for key, size, _ in await self._files.list_prefix(
            f"tenants/{only}" if only else "tenants/"
        ):
            try:
                tenant, path = split_key(key)
            except TenancyError:
                continue
            row = found.setdefault(
                tenant,
                {
                    "tenant_id": tenant,
                    "used_bytes": 0,
                    "quota_bytes": self._quota,
                    "_conversations": set(),
                },
            )
            row["used_bytes"] += size
            parts = path.split("/")
            if len(parts) > 3 and parts[0] == "users" and parts[2] == "conversations":
                row["_conversations"].add((parts[1], parts[3]))
        return [
            {
                **{k: v for k, v in row.items() if k != "_conversations"},
                "conversation_count": len(row["_conversations"]),
            }
            for _, row in sorted(found.items())
        ]

    async def conversations(self, tenant_id: str) -> list[dict]:
        """`conversation_id`, size and file count of what each conversation's sandbox wrote."""
        sizes: dict[str, list[int]] = {}
        for key, size, _ in await self._files.list_prefix(f"tenants/{tenant_id}/users"):
            try:
                _, path = split_key(key)
            except TenancyError:
                continue
            parts = path.split("/")
            if len(parts) > 4 and parts[0] == "users" and parts[2] == "conversations":
                totals = sizes.setdefault(parts[3], [0, 0])
                totals[0] += size
                totals[1] += 1
        return [
            {"conversation_id": cid, "size_bytes": size, "file_count": count}
            for cid, (size, count) in sorted(sizes.items())
        ]
