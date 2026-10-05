"""PrefixSync — an agent's home or a group's drive, as the sandbox sees it.

Their files are the object tree under ``conversation_shared_prefix(...)``: what uploads, the Files list and the Storage page all use. The sandbox
needs a real directory, so this keeps a scratch directory in step with that tree: before a run, what changed in the store is brought in; after
it, what the run changed is sent back. There is no snapshot to commit, because there is no branch to keep apart.

What a run changed is found by comparing the directory with a record of the last sync (kept outside the directory, where the code cannot touch
it): a file is sent when its size or modification time is not what the record says, and removed from the store when it is gone. What the store
changed is found the same way, from its listing. Files that did not change are not moved. Paths with a leading dot (``.previews``) belong to the
platform, not the workspace, and are neither brought in nor sent.

The code that ran here is untrusted, so the directory is read and written with care: a symlink or special file it left behind is removed before
anything is written (a write through a symlinked directory would land outside it), and nothing is read through one.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

from substrate.stores import FileStore, WorkspaceQuotaExceededError
from substrate.workspace.materialize import regular_files

logger = logging.getLogger(__name__)

# The largest file the workspace keeps; a bigger one the code makes is left out and the agent is told.
MAX_FILE_BYTES = 100 * 1024 * 1024


def _relative(key: str, prefix: str) -> str | None:
    """The path of *key* inside the workspace, or ``None`` for one that is not a workspace file (a folder marker, a hidden path, a path that climbs)."""
    rel = key.removeprefix(prefix)
    parts = PurePosixPath(rel).parts
    if not rel or rel.endswith("/") or any(p in ("..", ".") or p.startswith(".") for p in parts):
        return None
    return rel


def _hidden(rel: str) -> bool:
    return any(p.startswith(".") for p in PurePosixPath(rel).parts)


class PrefixSync:
    """Keeps scratch directories in step with object prefixes. One instance serves every workspace; ``state_root`` holds the records."""

    def __init__(self, store: FileStore, state_root: Path) -> None:
        self._store = store
        self._state_root = state_root

    # ── the record of the last sync ─────────────────────────────────────────

    def _record_path(self, state_key: str) -> Path:
        return self._state_root / f"{state_key}.json"

    def _load(self, state_key: str) -> dict[str, dict[str, list]]:
        try:
            return json.loads(self._record_path(state_key).read_text(encoding="utf-8"))["files"]
        except (OSError, ValueError, KeyError):
            return {}  # no record (or a damaged one): everything is treated as new, which costs a download, not a file

    def _save(self, state_key: str, files: dict[str, dict[str, list]]) -> None:
        path = self._record_path(state_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"files": files}), encoding="utf-8")
        tmp.replace(path)

    def forget(self, state_key: str) -> None:
        """Drop the record (the workspace was deleted)."""
        self._record_path(state_key).unlink(missing_ok=True)

    async def _listing(self, prefix: str) -> dict[str, tuple[int, float]]:
        found: dict[str, tuple[int, float]] = {}
        for key, size, mtime in await self._store.list_prefix(prefix):
            rel = _relative(key, prefix)
            if rel is not None:
                found[rel] = (size, mtime)
        return found

    # ── before the run ──────────────────────────────────────────────────────

    async def stage_in(self, prefix: str, scratch: Path, state_key: str) -> list[str]:
        """Bring what changed in the store into *scratch*. Returns the files that could not be brought in, each with the reason."""
        scratch.mkdir(parents=True, exist_ok=True)
        _prune_links(scratch)
        remote = await self._listing(prefix)
        files = self._load(state_key)
        problems: list[str] = []

        for rel, (size, mtime) in remote.items():
            known = files.get(rel)
            path = scratch / rel
            if known and tuple(known["remote"]) == (size, mtime) and os.path.lexists(path):
                continue  # what is there is what the store has (or the code's own unsaved change, which is kept and sent again)
            try:
                _write_atomic(path, await self._store.download(prefix + rel))
                st = os.lstat(path)
                files[rel] = {"remote": [size, mtime], "local": [st.st_size, st.st_mtime_ns]}
            except Exception as exc:  # noqa: BLE001 - one file that cannot be brought in must not stop the run
                logger.warning("workspace stage-in: %s: %s", prefix + rel, exc)
                problems.append(f"{rel}: could not be brought in ({exc})")

        for rel in [r for r in files if r not in remote]:  # removed from the store since the last sync
            (scratch / rel).unlink(missing_ok=True)
            del files[rel]

        self._save(state_key, files)
        return problems

    # ── after the run ───────────────────────────────────────────────────────

    async def stage_out(self, prefix: str, scratch: Path, state_key: str) -> list[str]:
        """Send what the run changed to the store. Returns the files that could not be saved, each with the reason: the agent is told."""
        files = self._load(state_key)
        present: dict[str, tuple[int, int]] = {}
        for path in regular_files(scratch):
            rel = path.relative_to(scratch).as_posix()
            if not _hidden(rel):
                st = os.lstat(path)
                present[rel] = (st.st_size, st.st_mtime_ns)

        problems: list[str] = []
        sent: set[str] = set()
        quota_hit = False
        for rel, (size, mtime_ns) in sorted(present.items()):
            if rel in files and tuple(files[rel]["local"]) == (size, mtime_ns):
                continue
            if size > MAX_FILE_BYTES:
                problems.append(f"{rel}: not saved, larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
                continue
            if quota_hit:
                problems.append(f"{rel}: not saved, storage quota exceeded")
                continue
            try:
                await self._store.upload(
                    prefix + rel,
                    (scratch / rel).read_bytes(),
                    content_type=mimetypes.guess_type(rel)[0] or "application/octet-stream",
                )
                sent.add(rel)
            except WorkspaceQuotaExceededError:
                quota_hit = True
                problems.append(f"{rel}: not saved, storage quota exceeded")
            except Exception as exc:  # noqa: BLE001 - report it; the rest of the run's files are still saved
                logger.warning("workspace stage-out: %s: %s", prefix + rel, exc)
                problems.append(f"{rel}: not saved ({exc})")

        for rel in [r for r in files if r not in present]:  # removed by the run
            try:
                await self._store.delete(prefix + rel)
                del files[rel]
            except Exception as exc:  # noqa: BLE001
                logger.warning("workspace stage-out: delete %s: %s", prefix + rel, exc)
                problems.append(f"{rel}: not removed ({exc})")

        remote = await self._listing(prefix)
        for rel, local in present.items():
            if (rel in sent or rel in files) and rel in remote:
                files[rel] = {"remote": list(remote[rel]), "local": list(local)}
        self._save(state_key, files)
        return problems


def _prune_links(root: Path) -> None:
    """Remove anything under *root* that is not a directory or a regular file. Code that ran here can leave a symlink (or a pipe); writing through
    one that stands for a directory would put a file wherever it points."""
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = os.path.join(dirpath, name)
            mode = os.lstat(path).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                os.unlink(path)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


__all__ = ["MAX_FILE_BYTES", "PrefixSync"]
