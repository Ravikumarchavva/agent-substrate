"""Workspace file versioning: one snapshot lineage shared by both writers (the person in the editor, the agent's code).

The canonical working file always mirrors the latest version. A snapshot is a copy of its bytes in the same file store, and
the lineage (who wrote each, its hash, what it restored) is one `index.json` beside the snapshots. Snapshots live outside
`workspace/shared/` (a sibling `versions/`) so the sandbox cannot reach them and they are not listed as the user's files.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from loamz.engine.agents.kernel.ports.files import FileStore
from loamz.engine.agents.stores.workspace.layout import conversation_version_key
from loamz.engine.errors import NotFoundError
from loamz.server.agents.features.workspace.keys import VERSIONS_DIR

MANIFEST = "index.json"
_NON_VERSIONABLE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_versionable(name: str) -> bool:
    """Images are not editable, so they get no snapshots."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext not in _NON_VERSIONABLE_EXTS


@dataclass
class VersionEntry:
    seq: int
    author: str  # initial | user | agent | restore
    checksum_sha256: str
    size_bytes: int
    created_at: float
    version_key: str
    restored_from_seq: int | None = None


def version_dir(object_key: str) -> str:
    """`.../branches/<b>/workspace/shared/<path>` -> `.../branches/<b>/workspace/versions/<path>`; any other key is
    versioned next to itself."""
    p = PurePosixPath(object_key)
    parts = p.parts
    if (
        len(parts) >= 11
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
        and parts[6] == "branches"
        and parts[8] == "workspace"
        and parts[9] == "shared"
    ):
        return conversation_version_key(
            parts[1], parts[3], parts[5], str(PurePosixPath(*parts[10:])), parts[7]
        )
    return str(p.parent / VERSIONS_DIR / p.name)


def _version_key(object_key: str, seq: int) -> str:
    return f"{version_dir(object_key)}/{seq}{PurePosixPath(object_key).suffix}"


def _manifest_key(object_key: str) -> str:
    return f"{version_dir(object_key)}/{MANIFEST}"


async def list_versions(store: FileStore, object_key: str) -> list[VersionEntry]:
    """Every snapshot of `object_key`, oldest first."""
    try:
        raw = await store.download(_manifest_key(object_key))
    except NotFoundError:
        return []
    try:
        return [VersionEntry(**e) for e in json.loads(raw)]
    except ValueError, TypeError:
        return []


async def latest_version(store: FileStore, object_key: str) -> VersionEntry | None:
    versions = await list_versions(store, object_key)
    return versions[-1] if versions else None


async def record_version(
    store: FileStore,
    *,
    object_key: str,
    data: bytes,
    author: str,
    restored_from_seq: int | None = None,
) -> VersionEntry:
    """Snapshot `data` as the next version of `object_key`."""
    versions = await list_versions(store, object_key)
    seq = (versions[-1].seq + 1) if versions else 1
    entry = VersionEntry(
        seq=seq,
        author=author,
        checksum_sha256=sha256_hex(data),
        size_bytes=len(data),
        created_at=time.time(),
        version_key=_version_key(object_key, seq),
        restored_from_seq=restored_from_seq,
    )
    await store.upload(entry.version_key, data)
    await store.upload(
        _manifest_key(object_key),
        json.dumps([asdict(v) for v in [*versions, entry]]).encode(),
        content_type="application/json",
    )
    return entry


async def capture_bytes(
    store: FileStore, *, object_key: str, data: bytes, change_author: str = "agent"
) -> VersionEntry | None:
    """Ensure the current bytes are versioned: `initial` when there is no history, `change_author` when the content changed
    outside our save endpoints, nothing when already up to date."""
    latest = await latest_version(store, object_key)
    if latest is None:
        return await record_version(store, object_key=object_key, data=data, author="initial")
    if latest.checksum_sha256 != sha256_hex(data):
        return await record_version(store, object_key=object_key, data=data, author=change_author)
    return None
