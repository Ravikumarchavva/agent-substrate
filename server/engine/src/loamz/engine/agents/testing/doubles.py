"""Doubles of the kernel's outward ports, for tests: each passes its port's conformance suite.

``MemoryFileStore`` is a ``FileStore`` in a dict (Loamz Data's file store is the real one).
"""

from __future__ import annotations

import posixpath
import time

from loamz.engine.errors import InvalidRequestError, NotFoundError


class MemoryFileStore:
    """Keyed bytes in memory. Keys are normalised paths; one that climbs out of its root is refused."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, float]] = {}

    @staticmethod
    def _key(key: str) -> str:
        if not key or key.startswith("/") or "\\" in key or "\x00" in key:
            raise InvalidRequestError(f"not a relative key: {key!r}")
        normal = posixpath.normpath(key)
        if normal.startswith("..") or normal != key.rstrip("/"):
            raise InvalidRequestError(f"key {key!r} climbs out of its prefix")
        return normal

    @staticmethod
    def _prefix(prefix: str) -> str:
        return prefix if prefix.endswith("/") else prefix + "/"

    async def upload(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        self._objects[self._key(key)] = (bytes(data), time.time())

    async def download(self, key: str) -> bytes:
        try:
            return self._objects[self._key(key)][0]
        except KeyError:
            raise NotFoundError(f"no object at {key!r}") from None

    async def exists(self, key: str) -> bool:
        return self._key(key) in self._objects

    async def delete(self, key: str) -> None:
        self._objects.pop(self._key(key), None)

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        p = self._prefix(prefix)
        return sorted(
            (k, len(data), mtime) for k, (data, mtime) in self._objects.items() if k.startswith(p)
        )

    async def delete_prefix(self, prefix: str) -> int:
        p = self._prefix(prefix)
        doomed = [k for k in self._objects if k.startswith(p)]
        for k in doomed:
            del self._objects[k]
        return len(doomed)

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        src, dst = self._prefix(source_prefix), self._prefix(dest_prefix)
        copied = [(k, v) for k, v in self._objects.items() if k.startswith(src)]
        for k, (data, _) in copied:
            self._objects[dst + k[len(src) :]] = (data, time.time())
        return len(copied)

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        return f"memory://{self._key(key)}?expires_in={expires_in}"

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        p = f"tenants/{tenant_id}/"
        return sum(len(data) for k, (data, _) in self._objects.items() if k.startswith(p))


__all__ = ["MemoryFileStore"]
