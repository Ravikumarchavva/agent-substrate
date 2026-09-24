"""Local disk store for attachments that haven't been sent yet.

An attachment lives here from the moment it's uploaded in the composer
until the message carrying it is actually sent — only then does
``routes/chat_context.py`` copy it into the real ``ctx.file_store``
(SeaweedFS) and mark ``FileMetadata.promoted_at``. An attachment that's
never sent (removed from the composer, or the tab just closed) never
touches permanent storage at all; ``sweep_stale`` reclaims it.

Same method surface as ``S3FileStore`` for the operations both stores need
(``upload``/``download``/``delete``/``exists``) so callers in
``routes/files.py`` don't need to branch on which store they're holding —
just which ``FileMetadata.promoted_at`` state the row is in.
"""

from __future__ import annotations

import time
from pathlib import Path

from substrate.logger import setup_logging

logger = setup_logging()


class PendingFileStore:
    """Disk-backed store for not-yet-sent attachments, rooted at *root_dir*.

    *key* is the same value used for the eventual ``object_key`` — a
    server-constructed ``tenants/{tid}/...`` path (see
    ``agents/workspace/layout.py``), never client-supplied, so it's
    safe to use directly as a relative filesystem path.
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root.resolve() not in path.parents and path != self._root.resolve():
            raise ValueError(f"invalid pending-store key: {key!r}")
        return path

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def download(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every pending (not-yet-sent) attachment under *prefix*.

        Mirrors the real file store's ``delete_prefix`` (see
        ``integrations/gdpr/eraser.py::erase_user``) — without this, an
        attachment staged in the composer but never sent survived a GDPR
        erasure request entirely, since it never touches ``ctx.file_store``
        until promotion. Same rooted-directory-walk approach as
        ``sweep_stale``.
        """
        root = self._path(prefix)
        if not root.exists():
            return 0
        removed = 0
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def sweep_stale(self, *, older_than_seconds: float) -> int:
        """Delete every pending file whose mtime is older than the cutoff.
        Synchronous (plain filesystem walk) — called from a periodic
        background sweep, not per-request. Returns the count removed.
        Corresponding ``FileMetadata`` rows are the caller's own concern
        (see ``routes/files.py``'s startup sweep, which deletes rows for
        files this removes) — this only ever touches bytes on disk."""
        if not self._root.exists():
            return 0
        cutoff = time.time() - older_than_seconds
        removed = 0
        for path in self._root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed


__all__ = ["PendingFileStore"]
