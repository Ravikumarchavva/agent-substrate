"""Workspace file versioning — one snapshot lineage shared by both writers.

A workspace file (e.g. a code-interpreter-generated report) can change two ways: the human edits it in the side panel (author="user",
via the PUT save-back) or the agent rewrites it via code_interpreter (author="agent", captured lazily the next time the file is
served). Every state becomes a recoverable snapshot, so the two never silently clobber each other and any version can be restored.

The canonical working file (``object_key``) always mirrors the latest version. A snapshot is a copy of its bytes in the same file
store, and the lineage (who wrote each, its hash, what it restored) is one ``index.json`` beside the snapshots. There is no versions
table: the history lives where the file lives, whatever the store (the data service in production, a folder locally).

Snapshots deliberately live *outside* the working tree rather than in a hidden directory beside the file. They are system-managed
data, not the user's files, and keeping them in their own prefix makes that separation structural instead of a naming convention
every component has to remember: ``workspace/shared/`` is what the code interpreter mounts and what is listed as the user's files,
so a snapshot written under it would be sandbox-reachable and show up as a "file".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from substrate.types.errors import ObjectNotFoundError
from substrate.workspace.layout import conversation_version_key

# The directory snapshots live in, a sibling of ``shared/`` (routes/workspace.py::list_files hides it from the Storage browser).
VERSIONS_DIR = "versions"
MANIFEST = "index.json"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class VersionEntry:
    seq: int
    author: str  # initial | user | agent | restore
    checksum_sha256: str
    size_bytes: int
    created_at: float
    version_key: str
    # When author == "restore", the seq this snapshot was restored from.
    restored_from_seq: int | None = None


def _version_dir(object_key: str) -> str:
    """``tenants/{tid}/users/{uid}/conversations/{cid}/branches/{bid}/workspace/shared/{path}``
    → ``.../branches/{bid}/workspace/versions/{path}`` — a sibling of ``shared/`` (see ``workspace/layout.py``'s
    ``conversation_version_key``), holding one numbered file per snapshot plus the manifest.

    Keys that don't match the canonical conversation-workspace shape (e.g. a knowledge-base document) are versioned next to
    themselves — there's no per-user prefix to hang the snapshot off.
    """
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
        tenant_id, user_id, conversation_id, branch_id = (
            parts[1],
            parts[3],
            parts[5],
            parts[7],
        )
        rel_path = str(PurePosixPath(*parts[10:]))
        return conversation_version_key(
            tenant_id, user_id, conversation_id, rel_path, branch_id
        )
    return str(p.parent / VERSIONS_DIR / p.name)


def _version_key(object_key: str, seq: int) -> str:
    return f"{_version_dir(object_key)}/{seq}{PurePosixPath(object_key).suffix}"


def _manifest_key(object_key: str) -> str:
    return f"{_version_dir(object_key)}/{MANIFEST}"


async def list_versions(store: Any, object_key: str) -> list[VersionEntry]:
    """Every snapshot of *object_key*, oldest first."""
    try:
        raw = await store.download(_manifest_key(object_key))
    except (ObjectNotFoundError, FileNotFoundError, KeyError):
        return []
    try:
        return [VersionEntry(**e) for e in json.loads(raw)]
    except (ValueError, TypeError):
        return []


async def latest_version(store: Any, object_key: str) -> VersionEntry | None:
    versions = await list_versions(store, object_key)
    return versions[-1] if versions else None


async def record_version(
    store: Any,
    *,
    object_key: str,
    data: bytes,
    author: str,
    restored_from_seq: int | None = None,
) -> VersionEntry:
    """Snapshot ``data`` as the next version of ``object_key``."""
    versions = await list_versions(store, object_key)
    seq = (versions[-1].seq + 1) if versions else 1
    entry = VersionEntry(
        seq=seq,
        author=author,
        checksum_sha256=sha256_hex(data),
        size_bytes=len(data),
        created_at=datetime.now(timezone.utc).timestamp(),
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
    store: Any,
    *,
    object_key: str,
    data: bytes,
    change_author: str = "agent",
) -> VersionEntry | None:
    """Ensure the current canonical bytes are versioned. Records ``"initial"`` when there's no history yet, or ``change_author``
    (default ``"agent"``) when the content changed outside our save endpoints. No-op when already up to date. Callers that already
    hold the bytes (e.g. serve_file) pass them in to avoid a re-download."""
    checksum = sha256_hex(data)
    latest = await latest_version(store, object_key)
    if latest is None:
        return await record_version(
            store, object_key=object_key, data=data, author="initial"
        )
    if latest.checksum_sha256 != checksum:
        return await record_version(
            store, object_key=object_key, data=data, author=change_author
        )
    return None
