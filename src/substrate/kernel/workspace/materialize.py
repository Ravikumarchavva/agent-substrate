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
from pathlib import Path

from substrate.kernel.storage.snapshots import (
    WorkspaceFileEntry,
    WorkspaceManifest,
    WorkspaceSnapshot,
)

from .cas import BlobCAS


def _walk_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


async def materialize(cas: BlobCAS, manifest: WorkspaceManifest, dest: Path) -> None:
    """Populate *dest* with every file in *manifest*.

    Hardlinks from the CAS's local cache when available (near-instant, no
    byte copying); falls back to downloading through the CAS otherwise. A
    hardlink target is safe here because every write goes through
    ``commit()`` next, which reads bytes fresh and re-hashes — no code path
    mutates a materialized file's inode in place and expects the CAS's
    cached copy to change with it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for rel_path, entry in manifest.files.items():
        target = dest / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()

        cache_path = cas.cache_path(entry.content)
        linked = False
        if cache_path is not None and cache_path.exists():
            try:
                os.link(cache_path, target)
                linked = True
            except OSError:
                linked = False  # cross-device link or similar — fall back

        if not linked:
            data = await cas.get(entry.content)
            target.write_bytes(data)

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
    for path in sorted(_walk_files(root)):
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


__all__ = ["materialize", "commit"]
