"""StagedSandboxRuntime — materialize a branch's workspace snapshot before a
run, commit a new one after.

Exercises the real CAS/WorkspaceStore machinery (WorkspaceFileStore +
LocalFilesystemWorkspaceStore), not hand-rolled fakes, so these tests prove
the actual materialize/commit round trip works, not just that the wrapper
calls the right methods.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from substrate.agents.workspace import LocalFilesystemWorkspaceStore
from substrate.agents.workspace.scope import WorkspaceScope
from substrate.capabilities.storage.workspace import WorkspaceFileStore
from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes.base import (
    ExecResult,
    SandboxSpec,
)
from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes.staged import (
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
    from substrate.agents.workspace.cas import BlobCAS
    from substrate.agents.workspace.materialize import commit

    cas = BlobCAS(object_store, tenant_id=TENANT, user_id=USER)
    tmp_seed = Path(object_store._root) / ".seed"  # type: ignore[attr-defined]
    tmp_seed.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (tmp_seed / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_seed / name).write_bytes(data)
    parent = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    snap = await commit(cas, tmp_seed, session_id=CONVERSATION, branch_id=BRANCH, parent=parent)
    await ws_store.commit_snapshot(
        CONVERSATION, BRANCH, snap, expected_parent_snapshot_id=parent.id if parent else None
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


async def _runtime(tmp_path: Path, inner) -> tuple[StagedSandboxRuntime, WorkspaceFileStore, LocalFilesystemWorkspaceStore]:
    store = WorkspaceFileStore(tmp_path / "objects", user_quota_bytes=10_000_000)
    await store.connect()
    ws_store = LocalFilesystemWorkspaceStore(root=tmp_path / "ws_store")
    runtime = StagedSandboxRuntime(
        inner,
        object_store=store,
        workspace_store=ws_store,
        scratch_root=tmp_path / "scratch",
    )
    return runtime, store, ws_store


async def test_stage_in_materialises_the_branchs_committed_files(tmp_path, spec):
    runtime, store, ws_store = await _runtime(tmp_path, FakeInner(tmp_path / "scratch"))
    await _seed_branch(store, ws_store, {"data.csv": b"a,b\n1,2\n", "sub/notes.txt": b"hello"})

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


async def test_stage_out_commits_a_new_snapshot_with_only_the_changed_files(tmp_path, spec):
    inner = FakeInner(tmp_path / "scratch", outputs=[_inline("chart.png", b"PNG", "image/png")])
    runtime, store, ws_store = await _runtime(tmp_path, inner)
    await _seed_branch(store, ws_store, {"input.csv": b"untouched"})

    # The fake inner runtime doesn't actually write chart.png to disk, so
    # simulate what a real runtime would: the output file lands in scratch.
    async def _execute_and_write(spec):
        (tmp_path / "scratch" / SESSION_KEY / "chart.png").write_bytes(b"PNG")
        return ExecResult(stdout="ran", output_files=[_inline("chart.png", b"PNG", "image/png")])

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
    await _seed_branch(store, ws_store, {"a.txt": b"v1", "b.txt": b"from another writer"})
    snap_b = await ws_store.get_branch_snapshot_head(CONVERSATION, BRANCH)
    assert snap_b is not None and snap_b.id != snap_a.id

    from substrate.agents.workspace.cas import BlobCAS

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

    runtime, _store, _ws_store = await _runtime(tmp_path, SlowInner(tmp_path / "scratch"))

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
