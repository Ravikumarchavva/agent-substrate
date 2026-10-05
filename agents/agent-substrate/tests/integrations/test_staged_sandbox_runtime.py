"""StagedSandboxRuntime — materialize a branch's workspace snapshot before a
run, commit a new one after.

Exercises the real CAS/WorkspaceStore machinery (the store's files +
workspaces), not hand-rolled fakes, so these tests prove
the actual materialize/commit round trip works, not just that the wrapper
calls the right methods.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from substrate.workspace import Workspaces
from substrate.workspace import WorkspaceScope
from substrate.stores import Store
from substrate.stores.file_tables import Files
from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.base import (
    ExecResult,
    SandboxSpec,
)
from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.staged import (
    StagedSandboxRuntime,
)

TENANT = "t1"
USER = "u1"
CONVERSATION = "conv-1"
BRANCH = "main"
SESSION_KEY = f"{CONVERSATION}/{BRANCH}"


def _scope(**overrides) -> WorkspaceScope:
    defaults = dict(
        tenant_id=TENANT, user_id=USER, conversation_id=CONVERSATION, branch_id=BRANCH
    )
    defaults.update(overrides)
    return WorkspaceScope(**defaults)


class FakeInner:
    """Records the tree it saw at run time, and emits the given outputs."""

    name = "fake"

    def __init__(self, root: Path, outputs=None) -> None:
        self.root = root
        self.outputs = outputs or []
        self.seen_at_run: dict[str, bytes] = {}
        self.stopped = False
        self.calls = 0

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        self.calls += 1
        session = self.root / spec.session_dir
        if session.is_dir():
            self.seen_at_run = {
                p.relative_to(session).as_posix(): p.read_bytes()
                for p in session.rglob("*")
                if p.is_file()
            }
        return ExecResult(stdout="ran", output_files=list(self.outputs))

    async def stop(self) -> None:
        self.stopped = True


def _inline(name: str, data: bytes, mime: str = "text/plain") -> dict:
    return {
        "name": name,
        "mime_type": mime,
        "content_base64": base64.b64encode(data).decode(),
    }


async def _seed_branch(object_store, ws_store, files: dict[str, bytes]) -> None:
    """Commit a snapshot to main directly, bypassing the runtime — chains
    onto the branch's current head (if any), same as a real prior turn."""
    from substrate.workspace import BlobCAS
    from substrate.workspace.materialize import commit

    cas = BlobCAS(object_store, tenant_id=TENANT, user_id=USER)
    tmp_seed = object_store.store.root / ".seed"
    tmp_seed.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (tmp_seed / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_seed / name).write_bytes(data)
    parent = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    snap = await commit(
        cas, tmp_seed, session_id=CONVERSATION, branch_id=BRANCH, parent=parent
    )
    await ws_store.commit_snapshot(
        CONVERSATION,
        BRANCH,
        snap,
        expected_parent_snapshot_id=parent.id if parent else None,
    )


@pytest.fixture
def spec() -> SandboxSpec:
    return SandboxSpec(
        user_id=USER,
        thread_id=CONVERSATION,
        session_dir=SESSION_KEY,
        code="x=1",
        extra={"workspace_scope": _scope()},
    )


async def _runtime(
    tmp_path: Path, inner
) -> tuple[StagedSandboxRuntime, Files, Workspaces]:
    store = Store.at(tmp_path / "objects", file_quota_bytes=10_000_000).files
    ws_store = Workspaces(Store.at(tmp_path / "ws_store"))
    runtime = StagedSandboxRuntime(
        inner,
        object_store=store,
        workspace_store=ws_store,
        scratch_root=tmp_path / "scratch",
    )
    return runtime, store, ws_store


async def test_stage_in_materialises_the_branchs_committed_files(tmp_path, spec):
    runtime, store, ws_store = await _runtime(tmp_path, FakeInner(tmp_path / "scratch"))
    await _seed_branch(
        store, ws_store, {"data.csv": b"a,b\n1,2\n", "sub/notes.txt": b"hello"}
    )

    await runtime.execute(spec)

    assert runtime._inner.seen_at_run == {  # type: ignore[attr-defined]
        "data.csv": b"a,b\n1,2\n",
        "sub/notes.txt": b"hello",
    }


async def test_fresh_branch_with_no_snapshot_gets_an_empty_scratch_dir(tmp_path, spec):
    inner = FakeInner(tmp_path / "scratch")
    runtime, _store, _ws_store = await _runtime(tmp_path, inner)

    result = await runtime.execute(spec)

    assert inner.seen_at_run == {}
    assert result.ok


async def test_stage_out_commits_a_new_snapshot_with_only_the_changed_files(
    tmp_path, spec
):
    inner = FakeInner(
        tmp_path / "scratch", outputs=[_inline("chart.png", b"PNG", "image/png")]
    )
    runtime, store, ws_store = await _runtime(tmp_path, inner)
    await _seed_branch(store, ws_store, {"input.csv": b"untouched"})

    # The fake inner runtime doesn't actually write chart.png to disk, so
    # simulate what a real runtime would: the output file lands in scratch.
    async def _execute_and_write(spec):
        (tmp_path / "scratch" / SESSION_KEY / "chart.png").write_bytes(b"PNG")
        return ExecResult(
            stdout="ran", output_files=[_inline("chart.png", b"PNG", "image/png")]
        )

    inner.execute = _execute_and_write  # type: ignore[method-assign]

    result = await runtime.execute(spec)

    assert result.workspace_snapshot_id is not None
    head = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert head is not None and head.id == result.workspace_snapshot_id
    assert head.manifest is not None
    assert set(head.manifest.files) == {"input.csv", "chart.png"}


async def test_commit_conflict_does_not_fail_the_run(tmp_path, spec):
    """A racing writer on the same branch must not surface as a tool failure
    — the user still gets stdout; the file change is just not durably saved.

    Simulated directly at the _stage_out level: this run's stage-in
    observed snapshot A as the parent, but by the time it commits, another
    writer has already advanced the real head to snapshot B — exactly the
    check-then-act race expected_parent_id exists to catch (see
    snapshots.py::commit_turn's docstring)."""
    inner = FakeInner(tmp_path / "scratch")
    runtime, store, ws_store = await _runtime(tmp_path, inner)
    await _seed_branch(store, ws_store, {"a.txt": b"v1"})
    snap_a = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert snap_a is not None

    # Another writer commits to main, advancing the real head past snap_a.
    await _seed_branch(
        store, ws_store, {"a.txt": b"v1", "b.txt": b"from another writer"}
    )
    snap_b = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert snap_b is not None and snap_b.id != snap_a.id

    from substrate.workspace import BlobCAS

    cas = BlobCAS(store, tenant_id=TENANT, user_id=USER)
    scratch_dir = tmp_path / "scratch" / SESSION_KEY
    scratch_dir.mkdir(parents=True, exist_ok=True)
    (scratch_dir / "a.txt").write_bytes(b"modified locally")

    new_id = await runtime._stage_out(cas, _scope(), scratch_dir, snap_a.id)

    assert new_id is None  # conflict — not committed
    # The real head is still snap_b, untouched by the raced attempt.
    head = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert head is not None and head.id == snap_b.id


async def test_concurrent_runs_on_one_session_are_serialised(tmp_path, spec):
    order: list[str] = []

    class SlowInner(FakeInner):
        async def execute(self, spec):
            order.append("start")
            await asyncio.sleep(0.02)
            order.append("end")
            return ExecResult(stdout="ran")

    runtime, _store, _ws_store = await _runtime(
        tmp_path, SlowInner(tmp_path / "scratch")
    )

    await asyncio.gather(runtime.execute(spec), runtime.execute(spec))

    assert order == ["start", "end", "start", "end"]


async def test_stop_is_delegated_to_the_inner_runtime(tmp_path):
    inner = FakeInner(tmp_path / "scratch")
    runtime, _store, _ws_store = await _runtime(tmp_path, inner)

    await runtime.stop()

    assert inner.stopped


async def test_execute_requires_a_workspace_scope(tmp_path):
    inner = FakeInner(tmp_path / "scratch")
    runtime, _store, _ws_store = await _runtime(tmp_path, inner)
    bad_spec = SandboxSpec(
        user_id=USER, thread_id=CONVERSATION, session_dir=SESSION_KEY, code="x=1"
    )

    with pytest.raises(ValueError):
        await runtime.execute(bad_spec)


async def test_private_dir_is_never_committed_into_the_shared_manifest(tmp_path):
    """Regression guard: private_dir must not be a subpath of session_dir on
    the host, or materialize.py's commit() would sweep it into the branch's
    shared manifest — visible to every other agent on the branch, the
    opposite of "private"."""
    private_key = f".private/{CONVERSATION}/{BRANCH}/agent-1"
    spec_with_private = SandboxSpec(
        user_id=USER,
        thread_id=CONVERSATION,
        session_dir=SESSION_KEY,
        code="x=1",
        extra={"workspace_scope": _scope(), "private_dir": private_key},
    )

    class PrivateWritingInner(FakeInner):
        async def execute(self, spec):
            private_path = self.root / spec.extra["private_dir"]
            private_path.mkdir(parents=True, exist_ok=True)
            (private_path / "secret.txt").write_bytes(b"agent-only")
            (self.root / spec.session_dir).mkdir(parents=True, exist_ok=True)
            (self.root / spec.session_dir / "shared.txt").write_bytes(b"visible to all")
            return ExecResult(stdout="ran")

    inner = PrivateWritingInner(tmp_path / "scratch")
    runtime, store, ws_store = await _runtime(tmp_path, inner)

    result = await runtime.execute(spec_with_private)

    head = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert head is not None and head.manifest is not None
    assert set(head.manifest.files) == {"shared.txt"}
    assert "secret.txt" not in head.manifest.files
    assert result.ok


async def test_a_symlink_the_code_makes_is_not_followed_out_of_the_workspace(tmp_path, spec):
    """Code can make ``leak -> /some/host/file``. The host walks the tree after the run, so following it would read a file from outside the
    workspace and keep it in the user's storage."""
    secret = tmp_path / "host-secret.txt"
    secret.write_bytes(b"not for the workspace")

    class LinkingInner(FakeInner):
        async def execute(self, spec):
            session = self.root / spec.session_dir
            session.mkdir(parents=True, exist_ok=True)
            (session / "leak").symlink_to(secret)
            (session / "kept.txt").write_bytes(b"ordinary")
            return ExecResult(stdout="ran", output_files=[])

    runtime, _store, ws_store = await _runtime(tmp_path, LinkingInner(tmp_path / "scratch"))

    await runtime.execute(spec)

    head = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert head is not None and head.manifest is not None
    assert set(head.manifest.files) == {"kept.txt"}


async def test_a_file_the_code_edits_in_place_does_not_change_the_cached_original(tmp_path, spec):
    """Materialised files are copies: an in-place write must not reach the shared blob cache every later checkout reads from."""
    original = b"original bytes"

    class EditingInner(FakeInner):
        async def execute(self, spec):
            with open(self.root / spec.session_dir / "data.txt", "r+b") as f:
                f.write(b"EDITED!")
            return ExecResult(stdout="ran")

    runtime, store, ws_store = await _runtime(tmp_path, EditingInner(tmp_path / "scratch"))
    await _seed_branch(store, ws_store, {"data.txt": original})
    cas = runtime._cas_for(_scope())
    ref = await cas.put(original)  # warms the cache with the original

    await runtime.execute(spec)

    cached = cas.cache_path(ref)
    assert cached is not None and cached.read_bytes() == original


# -- an agent's home and a group's drive: the object tree itself, not a snapshot -----------------------------------------------------------

HOME = "dot-6f1c0a2e-0000-4000-8000-000000000001"
HOME_2 = "dot-6f1c0a2e-0000-4000-8000-000000000003"
GROUP = "group-6f1c0a2e-0000-4000-8000-000000000002"
GROUP_2 = "group-6f1c0a2e-0000-4000-8000-000000000004"


def _tree_spec(home: str = HOME, mounts: tuple = (), code: str = "x=1") -> SandboxSpec:
    return SandboxSpec(
        user_id=USER,
        thread_id=home,
        session_dir=f"{home}/main",
        code=code,
        extra={"workspace_scope": _scope(conversation_id=home)},
        mounts=mounts,
    )


def _mount(label: str, workspace: str):
    from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.base import Mount

    return Mount(label=label, session_dir=f"{workspace}/main", scope=_scope(conversation_id=workspace))


def _key(workspace: str, rel: str) -> str:
    from substrate.workspace.layout import conversation_shared_key

    return conversation_shared_key(TENANT, USER, workspace, rel)


class Writing(FakeInner):
    """Records what each workspace held at run time, then makes the given files (workspace session dir -> {path: bytes}) and removes the given ones."""

    def __init__(self, root: Path, writes=None, removes=None) -> None:
        super().__init__(root)
        self.writes = writes or {}
        self.removes = removes or {}
        self.seen: dict[str, dict[str, bytes]] = {}

    async def execute(self, spec: SandboxSpec) -> ExecResult:
        for session in [spec.session_dir, *(m.session_dir for m in spec.mounts)]:
            base = self.root / session
            self.seen[session] = (
                {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()} if base.is_dir() else {}
            )
            for rel, data in self.writes.get(session, {}).items():
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                (base / rel).write_bytes(data)
            for rel in self.removes.get(session, ()):
                (base / rel).unlink()
        return ExecResult(stdout="ran")


async def test_a_file_in_the_agents_files_is_there_for_the_code_and_what_the_code_makes_is_in_the_files(tmp_path):
    inner = Writing(tmp_path / "scratch", writes={f"{HOME}/main": {"out/chart.png": b"PNG"}})
    runtime, store, _ws = await _runtime(tmp_path, inner)
    await store.upload(_key(HOME, "uploads/in.txt"), b"hello")

    result = await runtime.execute(_tree_spec())

    assert inner.seen[f"{HOME}/main"] == {"uploads/in.txt": b"hello"}
    assert await store.download(_key(HOME, "out/chart.png")) == b"PNG"
    assert result.workspace_snapshot_id is None  # a tree has no snapshots to record


async def test_a_file_removed_on_either_side_is_removed_on_the_other(tmp_path):
    inner = Writing(tmp_path / "scratch", writes={f"{HOME}/main": {"keep.txt": b"k", "gone.txt": b"g"}})
    runtime, store, _ws = await _runtime(tmp_path, inner)
    await runtime.execute(_tree_spec())  # both files are saved

    await store.delete(_key(HOME, "gone.txt"))  # the user deletes one from the Files list
    inner.writes = {}
    await runtime.execute(_tree_spec())
    assert inner.seen[f"{HOME}/main"] == {"keep.txt": b"k"}  # the code no longer finds it

    inner.removes = {f"{HOME}/main": ["keep.txt"]}  # the code deletes the other
    await runtime.execute(_tree_spec())
    assert not await store.exists(_key(HOME, "keep.txt"))


async def test_files_that_did_not_change_are_not_transferred_again(tmp_path):
    inner = Writing(tmp_path / "scratch", writes={f"{HOME}/main": {"a.txt": b"a"}})
    runtime, store, _ws = await _runtime(tmp_path, inner)
    await runtime.execute(_tree_spec())

    moved: list[str] = []
    download, upload = store.download, store.upload

    async def counting_download(key: str) -> bytes:
        moved.append(f"get {key}")
        return await download(key)

    async def counting_upload(key: str, data: bytes, **kw) -> None:
        moved.append(f"put {key}")
        await upload(key, data, **kw)

    store.download, store.upload = counting_download, counting_upload  # type: ignore[method-assign]
    inner.writes = {}
    await runtime.execute(_tree_spec())

    assert moved == []


async def test_hidden_paths_stay_out_of_the_workspace(tmp_path):
    inner = Writing(tmp_path / "scratch")
    runtime, store, _ws = await _runtime(tmp_path, inner)
    await store.upload(_key(HOME, ".previews/report.pdf.png"), b"thumb")  # kept beside the files for the UI, not part of them
    await store.upload(_key(HOME, "report.pdf"), b"pdf")

    await runtime.execute(_tree_spec())

    assert inner.seen[f"{HOME}/main"] == {"report.pdf": b"pdf"}


async def test_a_mounted_workspace_is_seen_by_the_code_and_saved_to_its_own_files(tmp_path):
    home, drive = f"{HOME}/main", f"{GROUP}/main"
    inner = Writing(tmp_path / "scratch", writes={home: {"notes.md": b"mine"}, drive: {"plan.md": b"shared"}})
    runtime, store, _ws = await _runtime(tmp_path, inner)
    await store.upload(_key(GROUP, "uploads/brief.pdf"), b"brief")

    await runtime.execute(_tree_spec(mounts=(_mount("trip", GROUP),)))

    assert inner.seen[drive] == {"uploads/brief.pdf": b"brief"}
    assert await store.download(_key(GROUP, "plan.md")) == b"shared"
    assert await store.download(_key(HOME, "notes.md")) == b"mine"
    assert not await store.exists(_key(GROUP, "notes.md"))  # nothing crosses from one to the other
    assert not await store.exists(_key(HOME, "plan.md"))


async def test_runs_that_share_a_workspace_wait_for_each_other_whichever_they_open_first(tmp_path):
    order: list[str] = []

    class Slow(FakeInner):
        async def execute(self, spec):
            order.append(f"start {spec.thread_id}")
            await asyncio.sleep(0.02)
            order.append(f"end {spec.thread_id}")
            return ExecResult(stdout="ran")

    runtime, _store, _ws = await _runtime(tmp_path, Slow(tmp_path / "scratch"))
    one = _tree_spec(HOME, mounts=(_mount("a", GROUP), _mount("b", GROUP_2)))
    two = _tree_spec(HOME_2, mounts=(_mount("b", GROUP_2), _mount("a", GROUP)))  # the same two drives, the other way round

    await asyncio.wait_for(asyncio.gather(runtime.execute(one), runtime.execute(two)), timeout=5)

    assert order in (
        [f"start {HOME}", f"end {HOME}", f"start {HOME_2}", f"end {HOME_2}"],
        [f"start {HOME_2}", f"end {HOME_2}", f"start {HOME}", f"end {HOME}"],
    )


async def test_a_save_the_storage_quota_refuses_is_reported_to_the_agent(tmp_path):
    inner = Writing(tmp_path / "scratch", writes={f"{HOME}/main": {"big.bin": b"x" * 100}})
    store = Store.at(tmp_path / "objects", file_quota_bytes=10).files
    runtime = StagedSandboxRuntime(
        inner,
        object_store=store,
        workspace_store=Workspaces(Store.at(tmp_path / "ws_store")),
        scratch_root=tmp_path / "scratch",
    )

    result = await runtime.execute(_tree_spec())

    assert "big.bin" in result.stderr and "quota" in result.stderr.lower()
    assert result.stdout == "ran"  # the run itself is not failed over it


async def test_a_directory_the_code_swapped_for_a_symlink_cannot_redirect_what_is_written_next(tmp_path):
    """Code can replace ``docs/`` with a link to a host directory. The next stage-in writes into the workspace from outside the sandbox: it must not
    land in that directory."""
    outside = tmp_path / "outside"
    outside.mkdir()
    inner = Writing(tmp_path / "scratch")
    runtime, store, _ws = await _runtime(tmp_path, inner)
    await store.upload(_key(HOME, "docs/a.txt"), b"a")
    await runtime.execute(_tree_spec())

    docs = tmp_path / "scratch" / f"{HOME}/main" / "docs"
    for child in docs.iterdir():
        child.unlink()
    docs.rmdir()
    docs.symlink_to(outside)  # what the code left behind
    await store.upload(_key(HOME, "docs/b.txt"), b"b")
    await runtime.execute(_tree_spec())

    assert list(outside.iterdir()) == []
    assert inner.seen[f"{HOME}/main"] == {"docs/a.txt": b"a", "docs/b.txt": b"b"}
