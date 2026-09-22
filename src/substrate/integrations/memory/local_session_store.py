"""LocalFileSessionStore — durable ShortTermMemory backed by JSON files.

Stores per-session key-value state under ``<root>/<session_id>.json``.
Uses atomic file writes and per-session asyncio.Lock to guarantee consistent,
durable state across server restarts without requiring Postgres or Redis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class LocalFileSessionStore:
    """ShortTermMemory backed by a directory of JSON files.

    Parameters
    ----------
    root:
        Path to directory storing per-session JSON files. Defaults to
        ``./data/db/memory/short_term``.
    """

    def __init__(self, root: str | Path = "./data/db/memory/short_term") -> None:
        self._root = Path(root)
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def _session_file(self, session_id: str) -> Path:
        # Sanitize session_id to avoid path traversal
        clean_id = os.path.basename(session_id.strip())
        return self._root / f"{clean_id}.json"

    async def _get_lock(self, session_id: str) -> asyncio.Lock:
        async with self._global_lock:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]

    async def connect(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("LocalFileSessionStore connected (root=%s)", self._root)

    async def disconnect(self) -> None:
        pass

    async def get_state(self, session_id: str) -> dict[str, Any]:
        """Return the full state dict for *session_id* (empty dict if absent)."""
        path = self._session_file(session_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read session state from %s, returning empty", path)
            return {}

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Replace the entire state for *session_id* with *state*."""
        lock = await self._get_lock(session_id)
        async with lock:
            path = self._session_file(session_id)
            _atomic_write(path, state)

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        """Atomically merge *patch* into existing state — other keys preserved."""
        lock = await self._get_lock(session_id)
        async with lock:
            current = await self.get_state(session_id)
            current.update(patch)
            path = self._session_file(session_id)
            _atomic_write(path, current)

    async def clear(self, session_id: str) -> None:
        """Delete all state for *session_id*."""
        lock = await self._get_lock(session_id)
        async with lock:
            path = self._session_file(session_id)
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass


__all__ = ["LocalFileSessionStore"]

