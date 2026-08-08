"""WorkspaceFileStore — traversal guard, quota enforcement, usage accounting."""

from __future__ import annotations

import pytest

from substrate.capabilities.storage.workspace import (
    WorkspaceFileStore,
    WorkspacePathError,
    WorkspaceQuotaExceededError,
)


@pytest.fixture
async def store(tmp_path):
    fs = WorkspaceFileStore(root=tmp_path, user_quota_bytes=1000)
    await fs.connect()
    return fs


async def test_upload_download_round_trip(store):
    key = "users/u1/sessions/t1/hello.txt"
    await store.upload(key, b"hello world", content_type="text/plain")
    assert await store.download(key) == b"hello world"


async def test_download_missing_raises_keyerror(store):
    with pytest.raises(KeyError):
        await store.download("users/u1/sessions/t1/missing.txt")


async def test_delete_removes_file_and_prunes_empty_dirs(store, tmp_path):
    key = "users/u1/sessions/t1/hello.txt"
    await store.upload(key, b"data")
    await store.delete(key)
    with pytest.raises(KeyError):
        await store.download(key)
    # sessions/t1 and sessions dirs should be pruned (empty), users/u1 too,
    # but the root itself must remain.
    assert not (tmp_path / "users" / "u1").exists()
    assert tmp_path.exists()


@pytest.mark.parametrize(
    "bad_key",
    [
        "../escape.txt",
        "/etc/passwd",
        "users/u1/../../escape.txt",
        "",
    ],
)
async def test_traversal_rejected(store, bad_key):
    with pytest.raises(WorkspacePathError):
        await store.upload(bad_key, b"x")


async def test_traversal_rejected_on_download_and_delete(store):
    with pytest.raises(WorkspacePathError):
        await store.download("../../etc/passwd")
    with pytest.raises(WorkspacePathError):
        await store.delete("../../etc/passwd")


async def test_quota_enforced(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 600)
    with pytest.raises(WorkspaceQuotaExceededError):
        await store.upload("users/u1/uploads/b.bin", b"y" * 500)


async def test_quota_is_per_user(store):
    await store.upload("users/u1/uploads/a.bin", b"x" * 900)
    # A different user has their own 1000-byte budget.
    await store.upload("users/u2/uploads/a.bin", b"y" * 900)


async def test_overwrite_does_not_double_count_against_quota(store):
    key = "users/u1/uploads/a.bin"
    await store.upload(key, b"x" * 900)
    # Re-uploading the same key replaces it in place — must not be
    # rejected as if it were 900 (existing) + 900 (new) = 1800 bytes.
    await store.upload(key, b"y" * 900)
    assert await store.usage_bytes("u1", force=True) == 900


async def test_usage_bytes_counts_files_written_outside_upload(store, tmp_path):
    # Simulates a file the sandbox wrote directly to the mounted volume,
    # bypassing store.upload() — usage_bytes must still see it since it
    # walks the filesystem, not an internal ledger.
    user_dir = tmp_path / "users" / "u1" / "sessions" / "t1"
    user_dir.mkdir(parents=True)
    (user_dir / "generated.csv").write_bytes(b"z" * 42)
    assert await store.usage_bytes("u1", force=True) == 42


async def test_presign_url_returns_sentinel(store):
    url = await store.presign_url("users/u1/uploads/a.bin")
    assert url == "workspace://users/u1/uploads/a.bin"


async def test_list_user_files(store):
    await store.upload("users/u1/sessions/t1/a.txt", b"aaa")
    await store.upload("users/u1/sessions/t2/b.txt", b"bb")
    await store.upload("users/u2/uploads/c.txt", b"c")

    files = await store.list_user_files("u1")
    keys = {key for key, _, _ in files}
    assert keys == {"users/u1/sessions/t1/a.txt", "users/u1/sessions/t2/b.txt"}
    sizes = {key: size for key, size, _ in files}
    assert sizes["users/u1/sessions/t1/a.txt"] == 3
    assert sizes["users/u1/sessions/t2/b.txt"] == 2
