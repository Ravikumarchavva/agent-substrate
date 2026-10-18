"""FileStore — keyed byte store contract.

The generic key/value object-storage shape every workspace/file-store
backend already informally implements (``Files``,
``S3FileStore`` in ``integrations/storage/``) — made an explicit kernel
Protocol so callers can depend on the shape instead of duck-typing it with
``hasattr(store, "copy_prefix")`` at each call site.

See ``kernel/storage/blob.py`` for how this differs from ``BlobStore``
(keyed vs. content-addressed — different jobs, both legitimate).

Keys are path-shaped strings (``tenants/{t}/users/{u}/...``); this Protocol
does not interpret them — key construction and parsing is an L1 concern
(``agents/workspace/layout.py``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class WorkspaceQuotaExceededError(Exception):
    """Raised when a write would push a tenant's usage past their quota."""

    def __init__(self, tenant_id: str, used_bytes: int, quota_bytes: int) -> None:
        self.tenant_id = tenant_id
        self.used_bytes = used_bytes
        self.quota_bytes = quota_bytes
        super().__init__(
            f"Storage quota exceeded for tenant {tenant_id!r}: {used_bytes} bytes used, {quota_bytes} byte quota"
        )


class WorkspacePathError(ValueError):
    """Raised for a key that is empty, absolute, or climbs out of its prefix."""


@runtime_checkable
class FileStore(Protocol):
    """Contract every keyed byte-storage backend must satisfy."""

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Write *data* at *key*, replacing any existing value."""
        ...

    async def download(self, key: str) -> bytes:
        """Read the bytes at *key*. Raises if absent."""
        ...

    async def exists(self, key: str) -> bool:
        """True if *key* is present. A point check, not a listing."""
        ...

    async def delete(self, key: str) -> None:
        """Remove *key*. A no-op if it doesn't exist."""
        ...

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(key, size_bytes, mtime)`` for every object under *prefix*."""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under *prefix*. Returns the count removed."""
        ...

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        """Copy every object under *source_prefix* to the same relative path
        under *dest_prefix*. Returns the count copied."""
        ...

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        """A URL a client can use to fetch *key* directly, valid for
        *expires_in* seconds."""
        ...

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        """Total bytes stored under this tenant's prefix."""
        ...


__all__ = ["FileStore", "WorkspacePathError", "WorkspaceQuotaExceededError"]
