"""LocalFilesystemShortTermMemory — JSON-file-backed session key/value state.

Stores data in a local directory tree (default: ``./data/db/short_term``),
the same "create a folder on first use, no external database" convention as
``local_history.py``/``local_vector.py``/etc. The canonical zero-infra
``ShortTermMemory`` (``kernel/storage/memory.py``) default — the Protocol's
own docstring names "local filesystem, Redis, Postgres" as the three
durable backends; this is the local-filesystem one.

Layout::

    <root>/
      sessions/<session_id>.json   — one file per session's flat state dict

Thread-safety: a single asyncio.Lock per session_id guards ``update_state``'s
read-merge-write so a concurrent patch never clobbers another's keys —
same per-key-lock convention as ``local_history.py``'s per-branch locks.
Writes are atomic (write-tmp-then-rename).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON to a file atomically (tmp -> rename) to avoid corruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class LocalFilesystemShortTermMemory:
    """Filesystem-backed ShortTermMemory — one JSON file per session."""

    def __init__(self, root: str | Path = "./data/db/short_term") -> None:
        self._root = Path(root)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._lock_map_lock = asyncio.Lock()

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._lock_map_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _path(self, session_id: str) -> Path:
        return self._root / "sessions" / f"{session_id}.json"

    def _read(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    async def get_state(self, session_id: str) -> dict[str, Any]:
        return self._read(session_id)

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        lock = await self._lock_for(session_id)
        async with lock:
            _atomic_write(self._path(session_id), state)

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        lock = await self._lock_for(session_id)
        async with lock:
            state = self._read(session_id)
            state.update(patch)
            _atomic_write(self._path(session_id), state)

    async def clear(self, session_id: str) -> None:
        lock = await self._lock_for(session_id)
        async with lock:
            path = self._path(session_id)
            path.unlink(missing_ok=True)


__all__ = ["LocalFilesystemShortTermMemory"]
