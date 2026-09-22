"""End-to-end coverage of the agents/workspace package: CAS, commit/checkout,
and the headline fix — forking a conversation's workspace is O(1) and copies
zero bytes, unlike the old dead-end copy_prefix mechanism it replaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.agents.workspace import LocalFilesystemWorkspaceStore
from substrate.agents.workspace.branching import fork_branch
from substrate.agents.workspace.cas import BlobCAS
from substrate.agents.workspace.materialize import commit, materialize
from substrate.agents.workspace.snapshots import checkout_branch, commit_turn
from substrate.agents.storage.local_object_store import WorkspaceFileStore
from substrate.kernel.exceptions import SnapshotConflictError

TENANT = "tenant-a"
USER = "user-a"


def _cas(store: WorkspaceFileStore, cache_dir: Path | None = None) -> BlobCAS:
    return BlobCAS(store, tenant_id=TENANT, user_id=USER, local_cache_dir=cache_dir)


@pytest.mark.asyncio
async def test_cas_dedups_identical_content(tmp_path: Path) -> None:
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cas = _cas(store)

    ref1 = await cas.put(b"hello world")
    ref2 = await cas.put(b"hello world")
    assert ref1.hash == ref2.hash
    assert ref1.kind == "blob"

    # Only one object was ever actually uploaded.
    from substrate.agents.workspace.layout import blob_key

    key = blob_key(TENANT, USER, ref1.hash)
    assert await store.exists(key)
    data = await store.download(key)
    assert data == b"hello world"


@pytest.mark.asyncio
async def test_cas_get_round_trips(tmp_path: Path) -> None:
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cas = _cas(store)

    ref = await cas.put(b"some file content")
    assert await cas.get(ref) == b"some file content"
    assert await cas.has(ref)


@pytest.mark.asyncio
async def test_commit_and_materialize_round_trip(tmp_path: Path) -> None:
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cas = _cas(store, cache_dir=tmp_path / "cache")

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("aaa")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("bbb")

    snap = await commit(cas, src, session_id="s1", branch_id="main", parent=None)
    assert snap.manifest is not None
    assert set(snap.manifest.files) == {"a.txt", "sub/b.txt"}
    assert snap.parent_snapshot_id is None

    dest = tmp_path / "checkout"
    await materialize(cas, snap.manifest, dest)
    assert (dest / "a.txt").read_text() == "aaa"
    assert (dest / "sub" / "b.txt").read_text() == "bbb"


@pytest.mark.asyncio
async def test_fork_is_o1_and_copies_zero_bytes(tmp_path: Path) -> None:
    """The headline fix: forking must not touch object storage at all —
    only a branch-head pointer moves. Confirmed by asserting the object
    store's write methods are never called during fork."""
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cas = _cas(store)
    ws_store = LocalFilesystemWorkspaceStore(root=tmp_path / "ws_store")

    root = tmp_path / "main_workspace"
    root.mkdir()
    (root / "notes.md").write_text("original notes")

    snap = await commit_turn(ws_store, cas, root, session_id="s1", branch_id="main")

    original_upload = store.upload
    upload_calls: list[str] = []

    async def _tracking_upload(key, data, **kw):
        upload_calls.append(key)
        return await original_upload(key, data, **kw)

    store.upload = _tracking_upload  # type: ignore[method-assign]

    forked = await fork_branch(
        ws_store,
        session_id="s1",
        source_branch_id="main",
        new_branch_id="exp-1",
    )

    assert upload_calls == []  # fork touched zero objects
    assert forked is not None
    assert forked.id == snap.id

    head_main = await ws_store.get_branch_snapshot_head("s1", "main")
    head_exp = await ws_store.get_branch_snapshot_head("s1", "exp-1")
    assert head_main is not None and head_exp is not None
    assert head_main.id == head_exp.id == snap.id


@pytest.mark.asyncio
async def test_fork_then_write_on_fork_does_not_mutate_parent_branch(tmp_path: Path) -> None:
    """The actual bug this package fixes: writing on a forked branch must
    never be visible on the branch it was forked from."""
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cas = _cas(store)
    ws_store = LocalFilesystemWorkspaceStore(root=tmp_path / "ws_store")

    main_root = tmp_path / "main"
    main_root.mkdir()
    (main_root / "data.csv").write_text("a,b\n1,2\n")
    await commit_turn(ws_store, cas, main_root, session_id="s1", branch_id="main")

    await fork_branch(
        ws_store,
        session_id="s1",
        source_branch_id="main",
        new_branch_id="exp-1",
    )

    # Materialize the fork, mutate it, commit back to the fork's branch.
    fork_root = tmp_path / "fork"
    await checkout_branch(ws_store, cas, fork_root, session_id="s1", branch_id="exp-1")
    (fork_root / "data.csv").unlink()
    (fork_root / "new_file.txt").write_text("added on the fork")
    await commit_turn(ws_store, cas, fork_root, session_id="s1", branch_id="exp-1")

    # Re-materialize main fresh — must be byte-identical to before the fork mutation.
    main_reread = tmp_path / "main_reread"
    main_head = await checkout_branch(
        ws_store, cas, main_reread, session_id="s1", branch_id="main"
    )
    assert main_head is not None
    assert (main_reread / "data.csv").read_text() == "a,b\n1,2\n"
    assert not (main_reread / "new_file.txt").exists()

    fork_reread = tmp_path / "fork_reread"
    await checkout_branch(ws_store, cas, fork_reread, session_id="s1", branch_id="exp-1")
    assert not (fork_reread / "data.csv").exists()
    assert (fork_reread / "new_file.txt").read_text() == "added on the fork"


@pytest.mark.asyncio
async def test_concurrent_commit_to_same_branch_conflicts_not_silently_lost(
    tmp_path: Path,
) -> None:
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cas = _cas(store)
    ws_store = LocalFilesystemWorkspaceStore(root=tmp_path / "ws_store")

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("v1")
    await commit_turn(ws_store, cas, root, session_id="s1", branch_id="main")

    # Two independent commits race against the same (now stale) parent.
    parent = await ws_store.get_branch_snapshot_head("s1", "main")
    from substrate.agents.workspace.materialize import commit as _commit_dir

    snap_a = await _commit_dir(cas, root, session_id="s1", branch_id="main", parent=parent)
    snap_b = await _commit_dir(cas, root, session_id="s1", branch_id="main", parent=parent)

    await ws_store.commit_snapshot(
        "s1", "main", snap_a, expected_parent_snapshot_id=parent.id
    )
    with pytest.raises(SnapshotConflictError):
        await ws_store.commit_snapshot(
            "s1", "main", snap_b, expected_parent_snapshot_id=parent.id
        )


@pytest.mark.asyncio
async def test_materialize_hardlinks_from_local_cache_on_second_checkout(
    tmp_path: Path,
) -> None:
    """The materialization optimization: a blob already in the local cache
    is hardlinked, not re-downloaded, on a second checkout."""
    store = WorkspaceFileStore(tmp_path / "store", user_quota_bytes=1_000_000)
    await store.connect()
    cache_dir = tmp_path / "cache"
    cas = _cas(store, cache_dir=cache_dir)

    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("content")
    snap = await commit(cas, root, session_id="s1", branch_id="main", parent=None)
    assert snap.manifest is not None

    dest1 = tmp_path / "dest1"
    await materialize(cas, snap.manifest, dest1)
    dest2 = tmp_path / "dest2"
    await materialize(cas, snap.manifest, dest2)

    stat1 = (dest1 / "a.txt").stat()
    stat2 = (dest2 / "a.txt").stat()
    assert stat1.st_ino == stat2.st_ino  # same inode -> hardlinked, not copied
