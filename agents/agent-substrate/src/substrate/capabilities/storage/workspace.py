"""Workspace-backed file store — a per-user directory tree on shared storage (L2).

Server-side only: the tree lives at ``root`` (a local dir in monolith dev, a
docker-compose volume, or a k8s RWX PVC mount in production — never on the
end user's machine). Keys are POSIX-relative paths of the form
``users/{user_id}/sessions/{thread_id}/{name}`` or ``users/{user_id}/uploads/{name}``;
callers (routes) build them from authenticated identity, never from raw
client input.

This is Phase 1 (single-tier): the filesystem tree IS the record, not a
cache in front of object storage. Quota enforcement here is soft/app-layer —
it protects against accidental runaway usage, not a hostile actor with
another path onto the same volume. The hard isolation boundary against other
users is the k8s ``subPath`` mount into each user's sandbox pod (see
``capabilities/tools/code_interpreter/code_interpreter/sandbox_service.py``),
not this quota check.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


class WorkspaceQuotaExceededError(Exception):
    """Raised when a write would push a user's usage past their quota."""

    def __init__(self, user_id: str, used_bytes: int, quota_bytes: int) -> None:
        self.user_id = user_id
        self.used_bytes = used_bytes
        self.quota_bytes = quota_bytes
        super().__init__(
            f"Storage quota exceeded for user {user_id!r}: "
            f"{used_bytes} bytes used, {quota_bytes} byte quota"
        )


class WorkspacePathError(ValueError):
    """Raised when a key resolves outside the workspace root."""


_USAGE_CACHE_TTL = 30.0  # seconds


class WorkspaceFileStore:
    """Async file store backed by a plain directory tree.

    Duck-types the same shape as ``S3FileStore``/``InMemoryFileStore``:
    ``upload``/``download``/``delete``/``presign_url``/``connect``/``disconnect``,
    plus workspace-specific helpers (``usage_bytes``, ``list_user_files``)
    used by the workspace management API.
    """

    def __init__(self, root: str | Path, user_quota_bytes: int) -> None:
        self._root = Path(root).resolve()
        self._quota_bytes = user_quota_bytes
        self._usage_cache: dict[str, tuple[float, int]] = {}

    async def connect(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    async def disconnect(self) -> None:
        pass

    def _resolve(self, key: str) -> Path:
        """Resolve *key* against the workspace root, rejecting traversal.

        Mirrors ``sandbox_runtime._resolve_workspace_path``'s
        realpath-and-commonpath check so both sides of the mount enforce the
        identical rule.
        """
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise WorkspacePathError(f"Invalid workspace key: {key!r}")
        candidate = (self._root / key).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise WorkspacePathError(f"Key escapes workspace root: {key!r}") from None
        return candidate

    @staticmethod
    def _user_id_from_key(key: str) -> str | None:
        parts = Path(key).parts
        if len(parts) >= 2 and parts[0] == "users":
            return parts[1]
        return None

    def usage_bytes(self, user_id: str, *, force: bool = False) -> int:
        """Sum of file sizes under ``users/{user_id}``, cached briefly.

        Walking the filesystem is the source of truth — it counts files the
        sandbox created directly, not just ones written through ``upload()``.
        """
        now = time.monotonic()
        cached = self._usage_cache.get(user_id)
        if not force and cached is not None and now - cached[0] < _USAGE_CACHE_TTL:
            return cached[1]

        user_root = self._root / "users" / user_id
        total = 0
        if user_root.is_dir():
            for dirpath, _dirnames, filenames in os.walk(user_root):
                for name in filenames:
                    try:
                        total += (Path(dirpath) / name).stat().st_size
                    except OSError:
                        continue
        self._usage_cache[user_id] = (now, total)
        return total

    def _invalidate_usage(self, user_id: str | None) -> None:
        if user_id is not None:
            self._usage_cache.pop(user_id, None)

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        del content_type  # plain files on disk; no per-object content-type store
        path = self._resolve(key)

        user_id = self._user_id_from_key(key)
        if user_id is not None:
            existing_size = path.stat().st_size if path.exists() else 0
            used = self.usage_bytes(user_id)
            if used - existing_size + len(data) > self._quota_bytes:
                raise WorkspaceQuotaExceededError(user_id, used, self._quota_bytes)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp-{os.getpid()}")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        self._invalidate_usage(user_id)

    async def download(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise KeyError(f"Object not found: {key}") from None

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        user_id = self._user_id_from_key(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        else:
            self._invalidate_usage(user_id)
            # Prune now-empty parent directories up to (not including) the root.
            parent = path.parent
            while parent != self._root and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        del expires_in
        # No real URL — caller detects "workspace://" and falls back to
        # /files/{id}/download, same convention as InMemoryFileStore's
        # "memory://" sentinel.
        return f"workspace://{key}"

    def list_user_files(self, user_id: str) -> list[tuple[str, int, float]]:
        """Yield ``(relative_key, size_bytes, mtime)`` for every file under
        ``users/{user_id}``, for the workspace management API."""
        user_root = self._root / "users" / user_id
        results: list[tuple[str, int, float]] = []
        if not user_root.is_dir():
            return results
        for dirpath, _dirnames, filenames in os.walk(user_root):
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    stat = full.stat()
                except OSError:
                    continue
                results.append(
                    (
                        full.relative_to(self._root).as_posix(),
                        stat.st_size,
                        stat.st_mtime,
                    )
                )
        return results
