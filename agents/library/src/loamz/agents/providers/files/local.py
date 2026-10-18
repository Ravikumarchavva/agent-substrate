"""A `FileStore` in a local folder, for a laptop or a single-machine deployment.

TODO(integration): with the [data] extra the container uses Loamz Data's file store (governed, versioned,
direct-to-storage) instead; this is the default when no data service is configured.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shutil
from pathlib import Path

from loamz.data.storage.errors import InvalidRequestError, NotFoundError


class LocalFileStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key or "\x00" in key:
            raise InvalidRequestError(f"not a relative key: {key!r}")
        normal = posixpath.normpath(key)
        if normal.startswith("..") or normal != key.rstrip("/"):
            raise InvalidRequestError(f"key {key!r} climbs out of its prefix")
        return self._root / normal

    async def upload(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)

        await asyncio.to_thread(write)

    async def download(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError, IsADirectoryError:
            raise NotFoundError(f"no object at {key!r}") from None

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _walk(self, prefix: str) -> list[tuple[str, int, float]]:
        base = self._path(prefix.rstrip("/"))
        if not base.is_dir():
            return []
        found = []
        for path in base.rglob("*"):
            if path.is_file() and not path.name.endswith(".part"):
                stat = path.stat()
                found.append((path.relative_to(self._root).as_posix(), stat.st_size, stat.st_mtime))
        return sorted(found)

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        return await asyncio.to_thread(self._walk, prefix)

    async def delete_prefix(self, prefix: str) -> int:
        count = len(await self.list_prefix(prefix))
        base = self._path(prefix.rstrip("/"))
        await asyncio.to_thread(shutil.rmtree, base, True)
        return count

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        src = source_prefix.rstrip("/") + "/"
        dst = dest_prefix.rstrip("/") + "/"
        objects = await self.list_prefix(source_prefix)
        for key, _, _ in objects:
            await self.upload(dst + key[len(src) :], await self.download(key))
        return len(objects)

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        raise NotImplementedError("a local folder has no direct URLs; the server streams the bytes")

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        return sum(size for _, size, _ in await self.list_prefix(f"tenants/{tenant_id}"))


__all__ = ["LocalFileStore"]
