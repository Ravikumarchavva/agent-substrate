"""S3-compatible file store backed by S3Connector (L2).

Keys use the same ``tenants/{tenant_id}/...`` layout as ``WorkspaceFileStore``
(see its module docstring and ``agents/workspace/layout.py``), so the two
are interchangeable behind ``ctx.file_store`` and the workspace management
API works against either. The store is addressed purely through the S3 API,
which is what makes the backend swappable — SeaweedFS locally (this stack's
default), or any other S3-compatible service in production — with no code
change.
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath

from substrate.capabilities.storage.workspace import WorkspaceQuotaExceededError
from substrate.infrastructure.storage.s3 import S3Connector

_USAGE_CACHE_TTL = 30.0  # seconds


def _tenant_id_from_key(key: str) -> str | None:
    """``tenants/{id}/rest`` -> ``id``; anything else -> ``None`` (unowned, so
    not charged to a quota). Every key under the current layout starts
    ``tenants/{tenant_id}/...`` — a conversation-scoped key carries no user
    segment at all, so tenant is the only identity reliably present. Same
    rule as ``WorkspaceFileStore._tenant_id_from_key``."""
    parts = PurePosixPath(key).parts
    if len(parts) >= 2 and parts[0] == "tenants":
        return parts[1]
    return None


class S3FileStore:
    """Async file store that delegates to S3Connector (aiobotocore-based).

    Compatible with SeaweedFS (docker-compose default) and real AWS S3.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        user_quota_bytes: int = 0,
    ) -> None:
        self._connector = S3Connector(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            default_bucket=bucket,
            region=region,
        )
        self._bucket = bucket
        self._quota_bytes = user_quota_bytes
        self._usage_cache: dict[str, tuple[float, int]] = {}

    async def connect(self) -> None:
        await self._connector.connect()
        await self._ensure_bucket()

    async def disconnect(self) -> None:
        await self._connector.disconnect()

    async def _ensure_bucket(self) -> None:
        """Create the bucket on first start if it doesn't exist."""
        import botocore.exceptions

        session = self._connector._session
        assert session is not None, "connector not connected"
        async with session.create_client(
            "s3",
            endpoint_url=self._connector._endpoint_url,
            aws_access_key_id=self._connector._access_key,
            aws_secret_access_key=self._connector._secret_key,
            region_name=self._connector._region,
        ) as client:
            try:
                # aiobotocore's dynamically-generated client methods aren't
                # typed precisely — pyright infers NoReturn for these calls.
                await client.head_bucket(Bucket=self._bucket)  # pyright: ignore[reportGeneralTypeIssues]
            except botocore.exceptions.ClientError:
                await client.create_bucket(Bucket=self._bucket)  # pyright: ignore[reportGeneralTypeIssues]

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        tenant_id = _tenant_id_from_key(key)
        if tenant_id is not None and self._quota_bytes > 0:
            # One listing yields both the prefix total and this key's current
            # size, so an overwrite is charged for its *delta* rather than
            # double-counted (mirrors WorkspaceFileStore.upload). Deliberately
            # uncached: a stale total here would let a write past the quota.
            entries = await self.list_prefix(f"tenants/{tenant_id}/")
            used = sum(size for _key, size, _mtime in entries)
            existing = next((s for k, s, _m in entries if k == key), 0)
            if used - existing + len(data) > self._quota_bytes:
                raise WorkspaceQuotaExceededError(tenant_id, used, self._quota_bytes)
        await self._connector.upload(
            key, data, content_type=content_type, bucket=self._bucket
        )
        self._invalidate_usage(tenant_id)

    async def download(self, key: str) -> bytes:
        return await self._connector.download(key, bucket=self._bucket)

    async def delete(self, key: str) -> None:
        await self._connector.delete(key, bucket=self._bucket)
        self._invalidate_usage(_tenant_id_from_key(key))

    async def delete_prefix(self, prefix: str) -> int:
        deleted = await self._connector.delete_prefix(prefix, bucket=self._bucket)
        self._usage_cache.clear()
        return deleted

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        """Copy every object under *source_prefix* to the same relative path
        under *dest_prefix* (for branch workspace forking).

        No native server-side copy on ``S3Connector`` yet, so this reads
        each object and re-uploads it through ``self.upload`` — which keeps
        quota accounting correct on the destination — rather than bypassing
        it via the raw connector. Mirrors ``WorkspaceFileStore.copy_prefix``.
        """
        entries = await self.list_prefix(source_prefix)
        src_root = source_prefix.rstrip("/")
        dest_root = dest_prefix.rstrip("/")
        copied = 0
        for key, _size, _mtime in entries:
            rel = key[len(src_root) :].lstrip("/")
            dest_key = f"{dest_root}/{rel}" if rel else dest_root
            data = await self.download(key)
            await self.upload(dest_key, data)
            copied += 1
        return copied

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        return await self._connector.presign_url(
            key, bucket=self._bucket, expires_in=expires_in
        )

    # ── workspace surface (mirrors WorkspaceFileStore, so the workspace
    # management API in serving/monolith/routes/workspace.py works against
    # either store) ──────────────────────────────────────────────────────────

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(key, size_bytes, mtime)`` for every object under *prefix* —
        the generic listing primitive (mirrors ``WorkspaceFileStore.list_prefix``)."""
        objects = await self._connector.list_objects(prefix=prefix, bucket=self._bucket)
        return [(o["key"], int(o["size"]), float(o["mtime"])) for o in objects]

    def _invalidate_usage(self, tenant_id: str | None) -> None:
        if tenant_id is not None:
            self._usage_cache.pop(tenant_id, None)

    async def exists(self, key: str) -> bool:
        """True if *key* is present. A HEAD, not a LIST — cheap point check,
        matching ``WorkspaceFileStore.exists``."""
        import botocore.exceptions

        async with self._connector._client_ctx() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)  # pyright: ignore[reportGeneralTypeIssues]
            except botocore.exceptions.ClientError:
                return False
            return True

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        """Sum of object sizes under ``tenants/{tenant_id}/``, cached briefly.

        Unlike the filesystem store this costs a paginated LIST, so the cache
        matters more here — but it is only ever read for *display*; quota
        enforcement in ``upload`` lists fresh.
        """
        now = time.monotonic()
        cached = self._usage_cache.get(tenant_id)
        if not force and cached is not None and now - cached[0] < _USAGE_CACHE_TTL:
            return cached[1]
        total = sum(
            size for _key, size, _mtime in await self.list_prefix(f"tenants/{tenant_id}/")
        )
        self._usage_cache[tenant_id] = (now, total)
        return total
