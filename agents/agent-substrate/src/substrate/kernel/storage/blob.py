"""BlobStore — content-addressed object/binary store contract.

``BlobStore`` and ``ObjectStore`` (``kernel/storage/objects.py``) are
deliberately different shapes over the same S3-compatible substrate:

- ``BlobStore`` is *content-addressed* — you hand it bytes, it hands back an
  opaque ``ref`` (the caller never picks the ref). Concrete implementation:
  ``DataRefArtifactStore`` (``integrations/pipeline/data_ref.py``), backed by
  Redis/S3 with TTL-based expiry.
- ``ObjectStore`` is *keyed* — the caller picks the key (a path-shaped
  string), and can list/copy/delete by prefix. Concrete implementations:
  ``WorkspaceFileStore`` (local filesystem) and ``S3FileStore``.

``WorkspaceFileStore`` does **not** implement ``BlobStore`` — it has no
``store``/``resolve``/``pin``/``unpin`` surface, only keyed upload/download.
An earlier version of this docstring claimed otherwise; that was never true.

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
