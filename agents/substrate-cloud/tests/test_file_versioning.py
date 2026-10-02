"""Workspace file versioning — the reconciliation lineage that lets the human
(panel edits) and the agent (code_interpreter rewrites) both change a file
without clobbering each other.

Exercises the ``file_versioning`` helpers against a real Postgres session with
an in-memory fake store, plus the pure path/type helpers from the route module.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from substrate.serving.monolith.file_versioning import (
    capture_bytes,
    latest_version,
    list_versions,
    record_version,
    sha256_hex,
    _version_key,
)
from substrate.serving.monolith.models import FileVersion
from substrate.serving.monolith.routes.workspace import _is_versionable


class _FakeStore:
    """Minimal async workspace store — dict-backed, mirrors upload/download."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def upload(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.blobs[key] = data

    async def download(self, key: str) -> bytes:
        try:
            return self.blobs[key]
        except KeyError:
            raise KeyError(key) from None


@pytest.fixture
async def db(database_url: str):
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(FileVersion.__table__.create, checkfirst=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


def _key() -> str:
    # Unique canonical key per test so rows never collide across runs —
    # the real tenants/{t}/users/{u}/conversations/{c}/branches/{b}/workspace/
    # shared/... shape, so these lineage tests exercise the same
    # _version_key branch production traffic does.
    return (
        f"tenants/tenant-a/users/u1/conversations/{uuid.uuid4().hex}"
        "/branches/main/workspace/shared/report.xlsx"
    )


async def _cleanup(db: AsyncSession, key: str) -> None:
    from sqlalchemy import delete

    await db.execute(delete(FileVersion).where(FileVersion.object_key == key))
    await db.commit()


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_version_key_layout():
    key = "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace/shared/report.xlsx"
    assert _version_key(key, 3) == (
        "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace"
        "/versions/report.xlsx/3.xlsx"
    )


def test_version_key_is_a_sibling_of_shared_not_nested_inside_it():
    """`shared/` is bind-mounted into the code-interpreter sandbox and
    enumerated as the user's files (routes/workspace.py::list_files) — a
    snapshot living inside it would be sandbox-reachable and show up as a
    "file". `versions/` must be a sibling of `shared/`, never nested under it."""
    key = "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace/shared/report.xlsx"
    version_key = _version_key(key, 1)
    assert "/workspace/shared/" not in version_key
    assert version_key.startswith(
        "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace/versions/"
    )


def test_version_key_has_no_hidden_path_segment():
    """A dot-prefixed segment is invisible to SeaweedFS's S3 LIST, which
    silently excluded snapshots from per-user usage totals."""
    key = "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace/shared/report.xlsx"
    assert not any(part.startswith(".") for part in _version_key(key, 1).split("/"))


def test_version_key_handles_nested_relative_paths():
    """The whole relative path below workspace/shared/ is preserved, so the
    mapping is total rather than flattening subfolders."""
    key = (
        "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace"
        "/shared/notes/draft.md"
    )
    assert _version_key(key, 2) == (
        "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace"
        "/versions/notes/draft.md/2.md"
    )


def test_version_keys_of_same_name_in_different_threads_do_not_collide():
    a = _version_key(
        "tenants/tenant-a/users/u1/conversations/t1/branches/main/workspace/shared/report.xlsx",
        1,
    )
    b = _version_key(
        "tenants/tenant-a/users/u1/conversations/t2/branches/main/workspace/shared/report.xlsx",
        1,
    )
    assert a != b


def test_version_key_falls_back_for_non_canonical_keys():
    """A key that isn't the canonical conversation-workspace shape (e.g. a
    knowledge-base document) still gets a deterministic, collision-free
    version key next to itself, rather than raising."""
    key = "tenants/tenant-a/knowledge/kb1/documents/doc1/source.pdf"
    assert _version_key(key, 1) == (
        "tenants/tenant-a/knowledge/kb1/documents/doc1/versions/source.pdf/1.pdf"
    )


def test_sha256_hex_stable():
    assert sha256_hex(b"abc") == sha256_hex(b"abc")
    assert sha256_hex(b"abc") != sha256_hex(b"abd")


def test_is_versionable():
    assert _is_versionable("report.xlsx")
    assert _is_versionable("notes.txt")
    assert _is_versionable("report.html")
    assert not _is_versionable("chart.png")
    assert not _is_versionable("photo.JPEG")


# ── lineage ───────────────────────────────────────────────────────────────────


async def test_record_version_increments_seq(db: AsyncSession):
    store = _FakeStore()
    key = _key()
    try:
        v1 = await record_version(db, store, object_key=key, data=b"one", author="user")
        v2 = await record_version(db, store, object_key=key, data=b"two", author="user")
        assert (v1.seq, v2.seq) == (1, 2)
        # Snapshots are stored under .versions and carry the right checksums.
        assert store.blobs[v1.version_key] == b"one"
        assert store.blobs[v2.version_key] == b"two"
        assert v2.checksum_sha256 == sha256_hex(b"two")
        latest = await latest_version(db, key)
        assert latest is not None and latest.seq == 2
    finally:
        await _cleanup(db, key)


async def test_capture_bytes_initial_then_agent_then_noop(db: AsyncSession):
    store = _FakeStore()
    key = _key()
    try:
        # First sight of the file → "initial".
        v1 = await capture_bytes(db, store, object_key=key, data=b"gen-by-agent")
        assert v1 is not None and v1.author == "initial" and v1.seq == 1

        # Unchanged content → no new version.
        assert (
            await capture_bytes(db, store, object_key=key, data=b"gen-by-agent") is None
        )

        # Content changed out-of-band (the agent rewrote it) → "agent".
        v2 = await capture_bytes(db, store, object_key=key, data=b"agent-edited")
        assert v2 is not None and v2.author == "agent" and v2.seq == 2

        versions = await list_versions(db, key)
        assert [v.author for v in versions] == ["initial", "agent"]
    finally:
        await _cleanup(db, key)


async def test_user_save_after_agent_change_is_a_new_version(db: AsyncSession):
    """The human/agent reconciliation core: agent change is captured, then the
    user's save is the newest version — nothing is lost."""
    store = _FakeStore()
    key = _key()
    try:
        await capture_bytes(db, store, object_key=key, data=b"v1")  # initial
        await capture_bytes(db, store, object_key=key, data=b"v2-agent")  # agent
        user = await record_version(
            db, store, object_key=key, data=b"v3-user", author="user"
        )
        assert user.seq == 3
        versions = await list_versions(db, key)
        assert [v.author for v in versions] == ["initial", "agent", "user"]
        # Every state remains recoverable from its snapshot.
        assert store.blobs[versions[0].version_key] == b"v1"
        assert store.blobs[versions[1].version_key] == b"v2-agent"
        assert store.blobs[versions[2].version_key] == b"v3-user"
    finally:
        await _cleanup(db, key)
