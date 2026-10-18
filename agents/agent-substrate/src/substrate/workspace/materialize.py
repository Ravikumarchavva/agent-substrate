"""Materialize a workspace snapshot into a directory, and commit a directory
back into a new snapshot.

The two halves of "check out a branch, run code, commit the result" — used
by the code interpreter (one materialize + one commit per turn) and by
anything else that needs a real directory view of a branch's files.

Deliberately does not import ``integrations/tools/code_interpreter/...`` —
``agents/`` (L1) cannot import ``integrations/`` (L2). The file-diffing here
is a small, independent, pure-stdlib equivalent of that package's
``runtimes/_files.py::snapshot()`` (same idea — mtime+size fingerprint — not
shared code, since neither side has a reason to depend on the other).
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from substrate.workspace.protocols import (
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
)

from .cas import BlobCAS


def regular_files(root: Path) -> list[Path]:
    """Every regular file under *root*. A symlink is skipped, not followed: code that ran in the workspace can leave ``leak -> /host/file``, and
    reading through it here, outside the sandbox, would take that file into the workspace."""
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if stat.S_ISREG(os.lstat(path).st_mode):
                found.append(path)
    return found


async def materialize(cas: BlobCAS, manifest: WorkspaceManifest, dest: Path) -> None:
    """Populate *dest* with every file in *manifest*.

    Copies from the CAS's local cache when available (no download); falls
    back to downloading through the CAS otherwise. Always a copy, never a
    hardlink: the code that runs next writes these files, possibly in place,
    and a shared inode would change the cached blob every later checkout reads.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for rel_path, entry in manifest.files.items():
        target = dest / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()

        cache_path = cas.cache_path(entry.content)
        if cache_path is not None and cache_path.exists():
            shutil.copyfile(cache_path, target)
        else:
            target.write_bytes(await cas.get(entry.content))

        os.chmod(target, entry.mode)


async def commit(
    cas: BlobCAS,
    root: Path,
    *,
    session_id: str,
    branch_id: str,
    parent: WorkspaceSnapshot | None,
) -> WorkspaceSnapshot:
    """Hash and upload every file under *root*, and build a new snapshot.

    Every file is re-read and re-hashed unconditionally — there is no
    mtime-based skip here (unlike the code interpreter's own before/after
    diff, which exists to decide what changed *within* a run). A commit
    happens once per turn, not per file-touch, so hashing the whole
    (typically small) tree is cheap and avoids trusting mtimes across a
    materialize/mutate/commit cycle that may span multiple sandbox runs.

    ``files`` is built in sorted-path order — the canonical ordering
    ``WorkspaceManifest`` requires for stable hashing and linear diff later.
    """
    entries: dict[str, WorkspaceFileEntry] = {}
    for path in sorted(regular_files(root)):
        rel_path = path.relative_to(root).as_posix()
        data = path.read_bytes()
        ref = await cas.put(data)
        mode = 0o755 if os.access(path, os.X_OK) else 0o644
        entries[rel_path] = WorkspaceFileEntry(path=rel_path, content=ref, mode=mode)

    manifest = WorkspaceManifest(files=entries)
    return WorkspaceSnapshot(
        session_id=session_id,
        branch_id=branch_id,
        parent_snapshot_id=parent.id if parent is not None else None,
        manifest=manifest,
    )


__all__ = ["materialize", "commit", "regular_files"]
