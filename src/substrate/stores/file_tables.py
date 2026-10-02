"""Files — keyed bytes, kept as a table of names beside a folder of contents.

``Files`` is the one implementation of ``FileStore`` (``stores/files.py``) for the folder store. A file is two things: a
row (its key, size, content type, owning tenant and the name of its contents) and a blob under ``files/``. The row is
what makes it exist, so the order of a write is what makes it safe:

1. the contents are written to a temporary file, flushed to disk, renamed into place, and the directory flushed;
2. only then is the row committed, in the same transaction that checks the tenant's quota;
3. only after that is the file it replaced removed.

A crash between steps leaves at worst an *unreferenced* blob — never a row that names contents which are not there — and
``collect_garbage`` removes those. Contents are never modified in place, so a reader holding a name always reads a whole
file, and copying a prefix is a hard link per file rather than a copy of the bytes.

Quota is metered per tenant (the second segment of a ``tenants/<tenant>/...`` key) as the exact sum of its files' sizes,
checked in the transaction that writes, so two uploads racing for the last bytes cannot both fit. It protects against
runaway use, not against a hostile process with another way onto the folder.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from substrate.stores.database import Row, Tx
from substrate.stores.files import WorkspacePathError, WorkspaceQuotaExceededError

if TYPE_CHECKING:
    from substrate.stores.store import Store

T = TypeVar("T")

SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS file_objects (
    seq {pk},
    key TEXT NOT NULL UNIQUE,
    tenant_id TEXT,
    blob TEXT NOT NULL,
    size INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    mtime DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS file_objects_tenant_idx ON file_objects (tenant_id);
"""
]


def _check(key: str) -> str:
    """A key is a ``/``-separated path that stays inside its prefix: not empty, not absolute, never ``..``."""
    if not key or key.startswith("/") or "\x00" in key or ".." in key.split("/"):
        raise WorkspacePathError(f"Invalid workspace key: {key!r}")
    return key


def _tenant_of(key: str) -> str | None:
    """The tenant a key belongs to — every key under the layout starts ``tenants/<tenant>/``; nothing else is a reliable
    identity to meter storage against."""
    parts = key.split("/")
    return parts[1] if len(parts) >= 2 and parts[0] == "tenants" and parts[1] else None


def _under(prefix: str) -> tuple[str, list[object]]:
    """The keys inside the directory ``prefix``: those that start with ``prefix/``."""
    stem = prefix.rstrip("/") or prefix
    return "substr(key, 1, ?) = ?", [len(stem) + 1, stem + "/"]


def _flush_to_disk(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so that it is on disk before the call returns: a crash afterwards loses nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)
    # a now-empty fan-out directory goes too, but never ``files/`` itself
    try:
        path.parent.rmdir()
    except OSError:
        pass


class Files:
    """The ``FileStore`` of a ``Store``: ``store.files``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        """The store these files live in — for a host that opened it and has to close it."""
        return self._store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        return await self._store.run(fn)

    def _path(self, blob: str) -> Path:
        return self._store.files_dir / blob[:2] / blob

    # ── quota ────────────────────────────────────────────────────────────────

    def effective_quota(self, tenant_id: str) -> int | None:
        """The quota that applies to ``tenant_id`` — their override if one is set, else the store's default; ``None`` is
        unlimited."""
        return self._store.file_quota_overrides.get(tenant_id, self._store.file_quota_bytes)

    def set_quota_override(self, tenant_id: str, quota_bytes: int | None) -> None:
        """Set (or, with ``None``, clear) ``tenant_id``'s quota override, effective on the next write. The host owns where
        an override is kept between runs and sets it again at startup."""
        if quota_bytes is None:
            self._store.file_quota_overrides.pop(tenant_id, None)
        else:
            self._store.file_quota_overrides[tenant_id] = quota_bytes

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        """Total bytes stored under this tenant. Always exact (``force`` exists for stores that cache)."""

        async def op(tx: Tx) -> int:
            row = await tx.fetchone("SELECT COALESCE(SUM(size), 0) AS used FROM file_objects WHERE tenant_id = ?", tenant_id)
            return int(row["used"])

        return await self._run(op)

    async def _enforce(self, tx: Tx, tenant_id: str | None, adding: int, replacing: int) -> None:
        quota = self.effective_quota(tenant_id) if tenant_id is not None else None
        if tenant_id is None or quota is None:
            return
        await tx.lock(f"file_quota:{tenant_id}")
        row = await tx.fetchone("SELECT COALESCE(SUM(size), 0) AS used FROM file_objects WHERE tenant_id = ?", tenant_id)
        used = int(row["used"])
        if used - replacing + adding > quota:
            raise WorkspaceQuotaExceededError(tenant_id, used, quota)

    # ── FileStore ────────────────────────────────────────────────────────────

    async def upload(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> None:
        _check(key)
        blob = uuid.uuid4().hex
        await asyncio.to_thread(_flush_to_disk, self._path(blob), data)  # the contents are on disk before any row names them
        tenant_id = _tenant_of(key)

        async def op(tx: Tx) -> str | None:
            previous = await tx.fetchone("SELECT blob, size FROM file_objects WHERE key = ?", key)
            await self._enforce(tx, tenant_id, len(data), previous["size"] if previous else 0)
            await tx.execute(
                "INSERT INTO file_objects (key, tenant_id, blob, size, content_type, mtime) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET tenant_id = excluded.tenant_id, blob = excluded.blob, "
                "size = excluded.size, content_type = excluded.content_type, mtime = excluded.mtime",
                key,
                tenant_id,
                blob,
                len(data),
                content_type,
                time.time(),
            )
            return previous["blob"] if previous else None

        try:
            replaced = await self._run(op)
        except BaseException:
            await asyncio.to_thread(_remove, self._path(blob))  # refused or failed: nothing may keep the contents
            raise
        if replaced:
            await asyncio.to_thread(_remove, self._path(replaced))

    async def download(self, key: str) -> bytes:
        _check(key)

        async def op(tx: Tx) -> Row | None:
            return await tx.fetchone("SELECT blob FROM file_objects WHERE key = ?", key)

        row = await self._run(op)
        if row is None:
            raise KeyError(f"Object not found: {key}")
        try:
            return await asyncio.to_thread(self._path(row["blob"]).read_bytes)
        except FileNotFoundError:
            raise KeyError(f"Object not found: {key}") from None

    async def exists(self, key: str) -> bool:
        try:
            _check(key)
        except WorkspacePathError:
            return False

        async def op(tx: Tx) -> bool:
            return await tx.fetchone("SELECT 1 FROM file_objects WHERE key = ?", key) is not None

        return await self._run(op)

    async def delete(self, key: str) -> None:
        _check(key)

        async def op(tx: Tx) -> str | None:
            row = await tx.fetchone("SELECT blob FROM file_objects WHERE key = ?", key)
            if row is None:
                return None
            await tx.execute("DELETE FROM file_objects WHERE key = ?", key)
            return row["blob"]

        blob = await self._run(op)
        if blob:
            await asyncio.to_thread(_remove, self._path(blob))

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        """``(key, size_bytes, mtime)`` for every file inside the directory ``prefix``."""
        try:
            _check(prefix)
        except WorkspacePathError:
            return []
        clause, params = _under(prefix)

        async def op(tx: Tx) -> list[tuple[str, int, float]]:
            rows = await tx.fetchall(f"SELECT key, size, mtime FROM file_objects WHERE {clause} ORDER BY key", *params)
            return [(row["key"], int(row["size"]), float(row["mtime"])) for row in rows]

        return await self._run(op)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete everything inside the directory ``prefix`` (or the one file of that name). Returns how many files."""
        _check(prefix)
        clause, params = _under(prefix)
        stem = prefix.rstrip("/") or prefix

        async def op(tx: Tx) -> list[str]:
            rows = await tx.fetchall(f"SELECT key, blob FROM file_objects WHERE key = ? OR {clause}", stem, *params)
            for row in rows:
                await tx.execute("DELETE FROM file_objects WHERE key = ?", row["key"])
            return [row["blob"] for row in rows]

        blobs = await self._run(op)
        for blob in blobs:
            await asyncio.to_thread(_remove, self._path(blob))
        return len(blobs)

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        """Copy every file inside ``source_prefix`` to the same relative path under ``dest_prefix`` (workspace forking).

        Each copy is a hard link to the same contents — contents are never modified in place — so forking a workspace
        costs a row per file, not its bytes. Returns the number of files copied; refuses the whole copy if it would
        push the destination's tenant past quota.
        """
        source, destination = source_prefix.rstrip("/") or source_prefix, dest_prefix.rstrip("/") or dest_prefix
        _check(source), _check(destination)
        clause, params = _under(source)
        tenant_id = _tenant_of(destination)
        linked: list[str] = []

        async def op(tx: Tx) -> int:
            rows = await tx.fetchall(f"SELECT key, blob, size, content_type FROM file_objects WHERE {clause} ORDER BY key", *params)
            targets = [(destination + row["key"][len(source) :], row) for row in rows]
            replacing = 0
            for target, _row in targets:
                previous = await tx.fetchone("SELECT size FROM file_objects WHERE key = ?", target)
                replacing += previous["size"] if previous else 0
            await self._enforce(tx, tenant_id, sum(row["size"] for _t, row in targets), replacing)
            for target, row in targets:
                blob = uuid.uuid4().hex
                await asyncio.to_thread(_link_or_copy, self._path(row["blob"]), self._path(blob))
                linked.append(blob)
                await tx.execute(
                    "INSERT INTO file_objects (key, tenant_id, blob, size, content_type, mtime) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET tenant_id = excluded.tenant_id, blob = excluded.blob, "
                    "size = excluded.size, content_type = excluded.content_type, mtime = excluded.mtime",
                    target,
                    _tenant_of(target),
                    blob,
                    row["size"],
                    row["content_type"],
                    time.time(),
                )
            return len(targets)

        try:
            return await self._run(op)
        except BaseException:
            for blob in linked:
                await asyncio.to_thread(_remove, self._path(blob))
            raise

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        # No real URL — the caller detects "workspace://" and falls back to its own download route.
        return f"workspace://{key}"

    # ── admin ────────────────────────────────────────────────────────────────

    async def list_all_tenants(self) -> list[str]:
        """Tenant ids that have stored a file."""

        async def op(tx: Tx) -> list[str]:
            rows = await tx.fetchall("SELECT DISTINCT tenant_id FROM file_objects WHERE tenant_id IS NOT NULL ORDER BY tenant_id")
            return [row["tenant_id"] for row in rows]

        return await self._run(op)

    async def list_conversations(self, tenant_id: str) -> list[tuple[str, int, int]]:
        """``(conversation_id, size_bytes, file_count)`` for every conversation workspace under
        ``tenants/<tenant>/users/*/conversations/`` — the admin storage drill-down. Conversation ids are thread UUIDs,
        globally unique, so no user segment is needed to tell them apart."""

        async def op(tx: Tx) -> list[tuple[str, int, int]]:
            rows = await tx.fetchall("SELECT key, size FROM file_objects WHERE tenant_id = ? ORDER BY key", tenant_id)
            totals: dict[tuple[str, str], list[int]] = {}
            for row in rows:
                parts = row["key"].split("/")
                if len(parts) > 6 and parts[2] == "users" and parts[4] == "conversations":
                    entry = totals.setdefault((parts[3], parts[5]), [0, 0])
                    entry[0] += int(row["size"])
                    entry[1] += 1
            return [(conversation, size, count) for (_user, conversation), (size, count) in sorted(totals.items())]

        return await self._run(op)

    async def collect_garbage(self, *, min_age_s: float = 3600.0) -> int:
        """Remove blobs no row names: what a crash between writing contents and committing the row leaves behind.

        Only blobs older than ``min_age_s`` are considered, because a blob written a moment ago by another process may
        be waiting for its row. Returns how many were removed.
        """

        async def op(tx: Tx) -> set[str]:
            return {row["blob"] for row in await tx.fetchall("SELECT blob FROM file_objects")}

        referenced = await self._run(op)

        def sweep() -> int:
            removed, cutoff = 0, time.time() - min_age_s
            for path in self._store.files_dir.glob("*/*"):
                if path.name in referenced:
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        _remove(path)
                        removed += 1
                except OSError:
                    continue
            return removed

        return await asyncio.to_thread(sweep)


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:  # a filesystem without hard links
        shutil.copyfile(source, destination)


__all__ = ["SCHEMA", "Files"]
