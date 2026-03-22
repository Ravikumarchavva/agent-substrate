"""Local filesystem FileStore driver.

Best for single-node Docker Compose and on-prem deployments that store
files on a mounted volume.  Path layout::

    {root}/
      org/{org_id}/user/{user_id}/thread/{thread_id}/{scope}/{filename}

All I/O is delegated to ``aiofiles`` (asyncio-friendly) with fallback to
``anyio.to_thread.run_sync`` for zero-copy reads on platforms where
aiofiles is unavailable.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import anyio

from agent_framework.core.storage.base import FileRef, FileStore


class LocalFileStore(FileStore):
    """Store files on the local filesystem.

    Parameters:
        root: Base directory.  Created automatically on ``startup()``.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    # ── lifecycle ────────────────────────────────────────────────────────

    async def startup(self) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._root.mkdir(parents=True, exist_ok=True)
        )

    # ── helpers ──────────────────────────────────────────────────────────

    def _abs(self, key: str) -> Path:
        """Resolve *key* to an absolute path inside the root.

        Raises ``ValueError`` if the resolved path escapes the root.
        """
        resolved = (self._root / key).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(f"Path traversal blocked: {key!r}")
        return resolved

    # ── write ────────────────────────────────────────────────────────────

    async def put(
        self,
        key: str,
        data: bytes | AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> FileRef:
        path = self._abs(key)
        await anyio.to_thread.run_sync(
            lambda: path.parent.mkdir(parents=True, exist_ok=True)
        )

        if isinstance(data, bytes):
            blob = data
        else:
            chunks: list[bytes] = []
            async for chunk in data:
                chunks.append(chunk)
            blob = b"".join(chunks)

        await anyio.to_thread.run_sync(path.write_bytes, blob)

        return FileRef(
            object_key=key,
            size_bytes=len(blob),
            content_type=content_type,
            checksum_sha256=self._sha256(blob),
            metadata=metadata or {},
        )

    # ── read ─────────────────────────────────────────────────────────────

    async def get(self, key: str) -> bytes:
        path = self._abs(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return await anyio.to_thread.run_sync(path.read_bytes)

    async def get_stream(
        self, key: str, chunk_size: int = 1024 * 256
    ) -> AsyncIterator[bytes]:
        path = self._abs(key)
        if not path.is_file():
            raise FileNotFoundError(key)

        async def _iter() -> AsyncIterator[bytes]:
            fd = await anyio.to_thread.run_sync(lambda: open(path, "rb"))
            try:
                while True:
                    chunk = await anyio.to_thread.run_sync(fd.read, chunk_size)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await anyio.to_thread.run_sync(fd.close)

        return _iter()

    async def get_url(self, key: str, *, expires_in: int = 3600) -> str:
        path = self._abs(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.as_uri()

    # ── metadata / existence ─────────────────────────────────────────────

    async def exists(self, key: str) -> bool:
        path = self._abs(key)
        return await anyio.to_thread.run_sync(path.is_file)

    async def head(self, key: str) -> FileRef:
        path = self._abs(key)
        if not path.is_file():
            raise FileNotFoundError(key)

        stat = await anyio.to_thread.run_sync(path.stat)
        blob = await anyio.to_thread.run_sync(path.read_bytes)

        return FileRef(
            object_key=key,
            size_bytes=stat.st_size,
            content_type="application/octet-stream",
            checksum_sha256=self._sha256(blob),
            created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
        )

    # ── delete ───────────────────────────────────────────────────────────

    async def delete(self, key: str) -> None:
        path = self._abs(key)
        if path.is_file():
            await anyio.to_thread.run_sync(path.unlink)

    async def delete_prefix(self, prefix: str) -> int:
        target = self._abs(prefix)

        if not target.exists():
            return 0

        if target.is_file():
            await anyio.to_thread.run_sync(target.unlink)
            return 1

        # target is a directory — recursively list files
        count = 0
        for root, _dirs, files in os.walk(target):
            for f in files:
                fp = Path(root) / f
                await anyio.to_thread.run_sync(fp.unlink)
                count += 1
        # clean up empty dirs
        await anyio.to_thread.run_sync(shutil.rmtree, target, True)
        return count

    # ── list ─────────────────────────────────────────────────────────────

    async def list_keys(
        self,
        prefix: str = "",
        *,
        limit: int = 1000,
        cursor: Optional[str] = None,
    ) -> tuple[list[str], Optional[str]]:
        target = self._abs(prefix) if prefix else self._root

        if not target.exists():
            return [], None

        # Gather all file keys under the prefix
        all_keys: list[str] = []
        for root, _dirs, files in os.walk(target):
            for f in files:
                abs_path = Path(root) / f
                rel = abs_path.relative_to(self._root).as_posix()
                all_keys.append(rel)

        all_keys.sort()

        # Simple cursor-based pagination (cursor = last key)
        if cursor:
            start_idx = 0
            for i, k in enumerate(all_keys):
                if k > cursor:
                    start_idx = i
                    break
            else:
                return [], None
            all_keys = all_keys[start_idx:]

        page = all_keys[:limit]
        next_cursor = page[-1] if len(all_keys) > limit else None
        return page, next_cursor

    # ── copy (override for efficiency) ───────────────────────────────────

    async def copy(self, src_key: str, dst_key: str) -> FileRef:
        src = self._abs(src_key)
        dst = self._abs(dst_key)
        if not src.is_file():
            raise FileNotFoundError(src_key)

        await anyio.to_thread.run_sync(
            lambda: dst.parent.mkdir(parents=True, exist_ok=True)
        )
        await anyio.to_thread.run_sync(shutil.copy2, src, dst)

        return await self.head(dst_key)
