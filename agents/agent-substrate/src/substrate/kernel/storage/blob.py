"""BlobStore — general-purpose object/binary store contract.

Concrete implementations:
  Stage 0 — in-memory dict (agents/runtime/local/)
  Stage 1 — S3-compatible adapter (adapters/storage/)

``store`` writes bytes or text and returns an opaque ref string.
``resolve`` fetches the original bytes by ref.
``pin`` / ``unpin`` control TTL: pinned refs survive past the default
expiry window, which matters for long-running chains that must not find
their own intermediates expired mid-execution.
"""

from __future__ import annotations

from typing import Protocol


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


__all__ = ["BlobStore"]
