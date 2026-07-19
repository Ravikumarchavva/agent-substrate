"""Workspace file versioning — one snapshot lineage shared by both writers.

A workspace file (e.g. a code-interpreter-generated report) can change two
ways: the human edits it in the side panel (author="user", via the PUT
save-back or the ONLYOFFICE callback) or the agent rewrites it via
code_interpreter (author="agent", captured lazily the next time the file is
served). Every state becomes a recoverable ``FileVersion`` snapshot, so the two
never silently clobber each other and any version can be restored.

The canonical working file (``object_key``) always mirrors the latest version;
snapshots are copies at ``.versions/{name}/{seq}{ext}`` in the same session dir.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.models import FileVersion

# Hidden per-file version store, kept out of the Storage listing (see
# routes/workspace.py::list_files, which filters this segment).
VERSIONS_DIR = ".versions"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version_key(object_key: str, seq: int) -> str:
    """`users/{uid}/sessions/{tid}/report.xlsx` →
    `users/{uid}/sessions/{tid}/.versions/report.xlsx/{seq}.xlsx`."""
    p = PurePosixPath(object_key)
    return str(p.parent / VERSIONS_DIR / p.name / f"{seq}{p.suffix}")


async def latest_version(db: AsyncSession, object_key: str) -> FileVersion | None:
    return (
        await db.execute(
            select(FileVersion)
            .where(FileVersion.object_key == object_key)
            .order_by(FileVersion.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_versions(db: AsyncSession, object_key: str) -> list[FileVersion]:
    return list(
        (
            await db.execute(
                select(FileVersion)
                .where(FileVersion.object_key == object_key)
                .order_by(FileVersion.seq.asc())
            )
        )
        .scalars()
        .all()
    )


async def record_version(
    db: AsyncSession,
    store: Any,
    *,
    object_key: str,
    data: bytes,
    author: str,
    user_id: str | None = None,
    thread_id: str | None = None,
    restored_from_seq: int | None = None,
) -> FileVersion:
    """Snapshot ``data`` as the next version of ``object_key`` and commit."""
    latest = await latest_version(db, object_key)
    seq = (latest.seq + 1) if latest else 1
    version_key = _version_key(object_key, seq)
    await store.upload(version_key, data)
    fv = FileVersion(
        object_key=object_key,
        seq=seq,
        version_key=version_key,
        author=author,
        restored_from_seq=restored_from_seq,
        checksum_sha256=sha256_hex(data),
        size_bytes=len(data),
        user_id=user_id,
        thread_id=thread_id,
    )
    db.add(fv)
    await db.commit()
    return fv


async def capture_bytes(
    db: AsyncSession,
    store: Any,
    *,
    object_key: str,
    data: bytes,
    user_id: str | None = None,
    thread_id: str | None = None,
    change_author: str = "agent",
) -> FileVersion | None:
    """Ensure the current canonical bytes are versioned. Records ``"initial"``
    when there's no history yet, or ``change_author`` (default ``"agent"``)
    when the content changed outside our save endpoints. No-op when already up
    to date. Callers that already hold the bytes (e.g. serve_file) pass them in
    to avoid a re-download."""
    checksum = sha256_hex(data)
    latest = await latest_version(db, object_key)
    if latest is None:
        return await record_version(
            db,
            store,
            object_key=object_key,
            data=data,
            author="initial",
            user_id=user_id,
            thread_id=thread_id,
        )
    if latest.checksum_sha256 != checksum:
        return await record_version(
            db,
            store,
            object_key=object_key,
            data=data,
            author=change_author,
            user_id=user_id,
            thread_id=thread_id,
        )
    return None
