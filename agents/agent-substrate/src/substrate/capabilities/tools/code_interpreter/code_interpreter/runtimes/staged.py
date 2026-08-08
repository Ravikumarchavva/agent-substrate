"""StagedSandboxRuntime — run a sandbox against a store that has no filesystem.

Every local runtime (bubblewrap, inprocess) executes with ``cwd`` inside a real
directory tree, and the k8s runtime mounts one. Object storage offers ``GET``/
``PUT`` on keys, not ``open()``/``write()``/``seek()``, so when
``FILE_STORE_BACKEND=s3`` there is no tree for them to run in.

This wrapper closes that gap by *materialising* the session before a run and
uploading what the run changed afterwards — the store stays the single source of
truth, while the sandbox still gets full POSIX semantics on a local scratch
tree. The alternative, FUSE-mounting the bucket (s3fs, mountpoint-s3), was
rejected deliberately: those clients have no random writes, no rename and no
append, so ordinary things like editing a file in place or ``pip install``ing
into the workspace fail in ways that are hard to explain to a user.

Scratch is disposable by construction: anything the run needs is pulled from the
store, and anything worth keeping is pushed back. That makes it safe for the
local tree to be a container's ephemeral disk.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path, PurePosixPath
from typing import Any

from substrate.logger import setup_logging

from .base import ExecResult, SandboxSpec

logger = setup_logging("substrate.code_interpreter.staged")


def _user_id_of(spec: SandboxSpec) -> str | None:
    """The owning user, preferring the spec and falling back to the session key.

    ``session_dir`` is ``users/{uid}/sessions/{tid}``, so it already carries the
    owner even when the caller left ``user_id`` unset.
    """
    if spec.user_id:
        return spec.user_id
    parts = PurePosixPath(spec.session_dir.strip("/")).parts
    if len(parts) >= 2 and parts[0] == "users":
        return parts[1]
    return None


class StagedSandboxRuntime:
    """Stage a session in from the file store, run *inner*, stage changes back.

    Delegates isolation entirely to *inner* — this class only moves bytes, and
    deliberately does not touch ``spec``, so the wrapped runtime enforces the
    same boundary it always did.
    """

    def __init__(
        self,
        inner: Any,
        *,
        file_store: Any,
        workspace_root: str | Path,
    ) -> None:
        self._inner = inner
        self._store = file_store
        self._root = Path(workspace_root).resolve()
        # One lock per session: two concurrent runs in the same thread would
        # otherwise interleave stage-in and stage-out and could upload a
        # half-written tree.
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def name(self) -> str:
        return f"staged({self._inner.name})"

    def _lock_for(self, session_dir: str) -> asyncio.Lock:
        lock = self._locks.get(session_dir)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_dir] = lock
        return lock

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        async with self._lock_for(spec.session_dir):
            await self._stage_in(spec)
            result = await self._inner.execute(spec)
            await self._stage_out(spec, result)
            return result

    async def stop(self) -> None:
        await self._inner.stop()

    # ── staging ──────────────────────────────────────────────────────────────

    async def _stage_in(self, spec: SandboxSpec) -> None:
        """Download the session's objects into the local scratch tree.

        Skips a key whose local copy already has the same size, so a warm
        scratch dir costs one LIST rather than a full re-download. Files present
        locally but absent from the store are left alone: they are almost always
        a previous run's output whose upload failed, and keeping them means the
        next stage-out retries rather than silently dropping the user's data.
        """
        user_id = _user_id_of(spec)
        if user_id is None:
            return
        prefix = spec.session_dir.strip("/") + "/"
        try:
            entries = await self._store.list_user_files(user_id)
        except Exception as exc:
            logger.warning("Stage-in listing failed for %s: %s", prefix, exc)
            return

        for key, size, _mtime in entries:
            if not key.startswith(prefix):
                continue
            local = self._root / key
            if local.is_file() and local.stat().st_size == size:
                continue
            try:
                data = await self._store.download(key)
            except Exception as exc:
                logger.warning("Stage-in download failed for %s: %s", key, exc)
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)

    async def _stage_out(self, spec: SandboxSpec, result: ExecResult) -> None:
        """Upload every file the run created or modified.

        ``output_files`` is already exactly that set — the local runtimes diff
        the tree (``_files.collect_changed``) and the k8s runtime's in-pod server
        reports the same shape — so there is no second walk here, and files the
        run only *read* are never re-uploaded.
        """
        for entry in result.output_files:
            name = str(entry.get("name") or "")
            if not name:
                continue
            key = f"{spec.session_dir.strip('/')}/{name}"
            data = self._entry_bytes(entry)
            if data is None:
                logger.warning("Stage-out: no content available for %s", key)
                continue
            try:
                await self._store.upload(
                    key,
                    data,
                    content_type=str(
                        entry.get("mime_type") or "application/octet-stream"
                    ),
                )
            except Exception as exc:
                # Never fail the tool call over this: the user still gets the
                # run's stdout and inline artifacts, and the file survives in
                # scratch for the next stage-out to retry.
                logger.warning("Stage-out upload failed for %s: %s", key, exc)

    def _entry_bytes(self, entry: dict[str, Any]) -> bytes | None:
        """Bytes for one output entry.

        Prefers the inline copy the runtime already produced; falls back to the
        host path, which is the only source for a file too large to inline
        (``too_large``) and is absent for the k8s runtime.
        """
        encoded = entry.get("content_base64")
        if isinstance(encoded, str):
            try:
                return base64.b64decode(encoded)
            except ValueError:
                return None
        raw_path = entry.get("path")
        if isinstance(raw_path, str) and raw_path:
            path = Path(raw_path)
            try:
                # Confine reads to the scratch root: `path` originates from a
                # runtime response, so it is not automatically trustworthy.
                path.resolve().relative_to(self._root)
            except ValueError:
                logger.warning("Stage-out: path outside workspace root: %s", raw_path)
                return None
            try:
                return path.read_bytes()
            except OSError:
                return None
        return None


__all__ = ["StagedSandboxRuntime"]
