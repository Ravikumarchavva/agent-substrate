"""FileStore abstract base class and FileRef value object.

Every storage backend (local, S3, Azure, GCS) implements ``FileStore``.
``FileRef`` is the immutable receipt returned after a successful ``put()``.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Optional


# ── Value Objects ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class FileRef:
    """Immutable receipt for a stored file.

    Attributes:
        object_key: Full path inside the store (includes tenant prefix).
        size_bytes: Total size in bytes.
        content_type: MIME type (e.g. ``"text/csv"``).
        checksum_sha256: Hex-encoded SHA-256 digest of the *cleartext* bytes.
        created_at: UTC timestamp of creation.
        metadata: Arbitrary k/v pairs persisted alongside the object.
    """

    object_key: str
    size_bytes: int
    content_type: str
    checksum_sha256: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, str] = field(default_factory=dict)


# ── Abstract Base Class ─────────────────────────────────────────────────────

class FileStore(ABC):
    """Backend-agnostic async file storage interface.

    Implementations MUST be safe for concurrent use from multiple asyncio
    tasks.  All paths are *object keys* — ``/``-delimited strings relative
    to the store root.

    Lifecycle:
        store = S3FileStore(...)
        await store.startup()    # acquire clients / pools
        ...
        await store.shutdown()   # release resources
    """

    # ── lifecycle ────────────────────────────────────────────────────────

    async def startup(self) -> None:  # noqa: B027
        """Acquire connections / clients.  Override if needed."""

    async def shutdown(self) -> None:  # noqa: B027
        """Release resources.  Override if needed."""

    # ── write ────────────────────────────────────────────────────────────

    @abstractmethod
    async def put(
        self,
        key: str,
        data: bytes | AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> FileRef:
        """Store *data* under *key* and return a ``FileRef``.

        Parameters:
            key: Full object key (caller is responsible for tenant-prefixing).
            data: Entire payload as ``bytes`` **or** an async byte-chunk iterator
                  for streaming / chunked uploads.
            content_type: MIME type to record.
            metadata: Optional key/value pairs stored alongside the object.

        Returns:
            A ``FileRef`` receipt with checksum, size, etc.
        """

    # ── read ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Return entire object contents as bytes.

        Raises ``FileNotFoundError`` if the key does not exist.
        """

    @abstractmethod
    async def get_stream(self, key: str, chunk_size: int = 1024 * 256) -> AsyncIterator[bytes]:
        """Yield the object contents in chunks.

        Raises ``FileNotFoundError`` if the key does not exist.
        """

    @abstractmethod
    async def get_url(self, key: str, *, expires_in: int = 3600) -> str:
        """Return a pre-signed or direct URL valid for *expires_in* seconds.

        For local stores this may return a ``file://`` URI or a server-relative
        path.  For S3/Azure it returns a pre-signed HTTPS URL.
        """

    # ── metadata / existence ─────────────────────────────────────────────

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return ``True`` if *key* exists in the store."""

    @abstractmethod
    async def head(self, key: str) -> FileRef:
        """Return metadata for *key* without downloading the body.

        Raises ``FileNotFoundError`` if the key does not exist.
        """

    # ── delete ───────────────────────────────────────────────────────────

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove *key* from the store.

        No-op (does not raise) if the key does not exist.
        """

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Remove all objects whose key starts with *prefix*.

        Returns the number of objects deleted.  Useful for purging an
        entire tenant or thread namespace.
        """

    # ── list ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def list_keys(
        self,
        prefix: str = "",
        *,
        limit: int = 1000,
        cursor: Optional[str] = None,
    ) -> tuple[list[str], Optional[str]]:
        """List object keys matching *prefix*.

        Returns ``(keys, next_cursor)`` for pagination.
        ``next_cursor`` is ``None`` when there are no more results.
        """

    # ── copy ─────────────────────────────────────────────────────────────

    async def copy(self, src_key: str, dst_key: str) -> FileRef:
        """Copy *src_key* to *dst_key* within the same store.

        Default implementation downloads-then-uploads; drivers should
        override with a server-side copy when available.
        """
        data = await self.get(src_key)
        ref = await self.head(src_key)
        return await self.put(
            dst_key,
            data,
            content_type=ref.content_type,
            metadata=ref.metadata,
        )

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _sha256(data: bytes) -> str:
        """Compute hex-encoded SHA-256 digest."""
        return hashlib.sha256(data).hexdigest()
