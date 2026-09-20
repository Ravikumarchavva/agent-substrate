"""BlobStore — general-purpose object/binary store contract.

Concrete implementations:
  Local / Standalone — Local filesystem blob store (WorkspaceFileStore in ./data/blobs/)
  Production / Cloud — S3-compatible object storage (SeaweedFS, MinIO, AWS S3)

``store`` writes bytes or text and returns an opaque ref string.
``resolve`` fetches the original bytes by ref.
``pin`` / ``unpin`` control TTL: pinned refs survive past the default
expiry window, which matters for long-running chains that must not find
their own intermediates expired mid-execution.
``exists`` / ``delete`` let callers check and reclaim a ref explicitly instead
of waiting on TTL expiry.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BlobStore(Protocol):
    """Object/binary store — the S3-compatible abstraction."""

    async def store(
        self,
        data: bytes | str,
        *,
        content_type: str = "application/octet-stream",
    ) -> str: ...

    async def resolve(self, ref: str) -> bytes: ...

    async def pin(self, ref: str) -> None: ...

    async def unpin(self, ref: str) -> None: ...

    async def exists(self, ref: str) -> bool:
        """True while ``ref`` still resolves (not deleted, not TTL-expired)."""
        ...

    async def delete(self, ref: str) -> bool:
        """Remove ``ref``'s data; returns whether anything was removed."""
        ...


__all__ = ["BlobStore"]
