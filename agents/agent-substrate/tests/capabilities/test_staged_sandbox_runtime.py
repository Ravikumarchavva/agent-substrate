"""StagedSandboxRuntime — materialise a session, run, upload what changed.

The point of the wrapper is that the object store stays the source of truth
while the sandbox still gets a real directory, so these tests assert on what
lands in the store and in the scratch tree rather than on call counts.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes.base import (
    ExecResult,
    SandboxSpec,
)
from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes.staged import (
    StagedSandboxRuntime,
)

SESSION = "users/u1/sessions/t1"


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.downloads: list[str] = []
        self.fail_upload_for: set[str] = set()
        self.fail_list = False

    async def list_user_files(self, user_id: str):
        if self.fail_list:
            raise RuntimeError("listing is down")
        return [
            (k, len(v), 1000.0)
            for k, v in self.objects.items()
            if k.startswith(f"users/{user_id}/")
        ]

    async def download(self, key: str) -> bytes:
        self.downloads.append(key)
        return self.objects[key]

    async def upload(self, key, data, *, content_type="") -> None:
        if key in self.fail_upload_for:
            raise RuntimeError("upload rejected")
        self.objects[key] = data
        self.uploads.append(key)


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


@pytest.fixture
def spec() -> SandboxSpec:
    return SandboxSpec(user_id="u1", thread_id="t1", session_dir=SESSION, code="x=1")


async def test_stage_in_materialises_stored_objects_before_the_run(tmp_path, spec):
    store = FakeStore()
    store.objects[f"{SESSION}/data.csv"] = b"a,b\n1,2\n"
    store.objects[f"{SESSION}/sub/notes.txt"] = b"hello"
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    # The sandbox saw the user's files, nested paths included.
    assert inner.seen_at_run == {"data.csv": b"a,b\n1,2\n", "sub/notes.txt": b"hello"}


async def test_stage_in_ignores_other_sessions_and_other_users(tmp_path, spec):
    store = FakeStore()
    store.objects[f"{SESSION}/mine.txt"] = b"mine"
    store.objects["users/u1/sessions/OTHER/theirs.txt"] = b"other session"
    store.objects["users/u2/sessions/t1/theirs.txt"] = b"other user"
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    assert list(inner.seen_at_run) == ["mine.txt"]


async def test_stage_out_uploads_only_what_the_run_changed(tmp_path, spec):
    store = FakeStore()
    store.objects[f"{SESSION}/input.csv"] = b"untouched"
    inner = FakeInner(tmp_path, outputs=[_inline("chart.png", b"PNG", "image/png")])
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    # A file the run merely read must not be re-uploaded.
    assert store.uploads == [f"{SESSION}/chart.png"]
    assert store.objects[f"{SESSION}/chart.png"] == b"PNG"


async def test_stage_in_skips_download_when_scratch_copy_matches(tmp_path, spec):
    store = FakeStore()
    store.objects[f"{SESSION}/data.csv"] = b"12345"
    local = tmp_path / SESSION / "data.csv"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"12345")  # same size ⇒ already warm
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    assert store.downloads == []


async def test_stage_in_redownloads_when_size_differs(tmp_path, spec):
    store = FakeStore()
    store.objects[f"{SESSION}/data.csv"] = b"newer and longer"
    local = tmp_path / SESSION / "data.csv"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"old")
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    assert store.downloads == [f"{SESSION}/data.csv"]
    assert inner.seen_at_run == {"data.csv": b"newer and longer"}


async def test_upload_failure_does_not_fail_the_run(tmp_path, spec):
    """The user still gets stdout and inline artifacts; the file stays in
    scratch so the next stage-out retries it."""
    store = FakeStore()
    store.fail_upload_for = {f"{SESSION}/chart.png"}
    inner = FakeInner(tmp_path, outputs=[_inline("chart.png", b"PNG", "image/png")])
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    result = await runtime.execute(spec)

    assert result.stdout == "ran"
    assert result.ok


async def test_listing_failure_does_not_fail_the_run(tmp_path, spec):
    store = FakeStore()
    store.fail_list = True
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    result = await runtime.execute(spec)

    assert result.ok
    assert inner.calls == 1


async def test_stage_out_reads_from_disk_when_content_is_not_inlined(tmp_path, spec):
    """A file over the inline cap carries no content_base64 — only a path."""
    store = FakeStore()
    big = tmp_path / SESSION / "big.bin"
    big.parent.mkdir(parents=True)
    big.write_bytes(b"L" * 32)
    inner = FakeInner(
        tmp_path,
        outputs=[
            {
                "name": "big.bin",
                "mime_type": "application/octet-stream",
                "content_base64": None,
                "too_large": True,
                "path": str(big),
            }
        ],
    )
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    assert store.objects[f"{SESSION}/big.bin"] == b"L" * 32


async def test_stage_out_refuses_a_path_outside_the_workspace_root(tmp_path, spec):
    """`path` comes from a runtime response, so it is not automatically
    trustworthy — a traversal must not exfiltrate a host file into the store."""
    outside = tmp_path.parent / "secret.txt"
    outside.write_bytes(b"host secret")
    store = FakeStore()
    inner = FakeInner(
        tmp_path,
        outputs=[
            {
                "name": "secret.txt",
                "mime_type": "text/plain",
                "content_base64": None,
                "path": str(outside),
            }
        ],
    )
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(spec)

    assert store.uploads == []


async def test_user_id_falls_back_to_the_session_key(tmp_path):
    """session_dir already carries the owner, so an unset user_id must not
    silently skip staging."""
    store = FakeStore()
    store.objects[f"{SESSION}/data.csv"] = b"x"
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(inner, file_store=store, workspace_root=tmp_path)

    await runtime.execute(
        SandboxSpec(user_id=None, thread_id="t1", session_dir=SESSION, code="x=1")
    )

    assert inner.seen_at_run == {"data.csv": b"x"}


async def test_concurrent_runs_on_one_session_are_serialised(tmp_path, spec):
    """Interleaved stage-in/stage-out could upload a half-written tree."""
    store = FakeStore()
    order: list[str] = []

    class SlowInner(FakeInner):
        async def execute(self, spec):
            order.append("start")
            await asyncio.sleep(0.02)
            order.append("end")
            return ExecResult(stdout="ran")

    runtime = StagedSandboxRuntime(
        SlowInner(tmp_path), file_store=store, workspace_root=tmp_path
    )

    await asyncio.gather(runtime.execute(spec), runtime.execute(spec))

    assert order == ["start", "end", "start", "end"]


async def test_stop_is_delegated_to_the_inner_runtime(tmp_path):
    inner = FakeInner(tmp_path)
    runtime = StagedSandboxRuntime(
        inner, file_store=FakeStore(), workspace_root=tmp_path
    )

    await runtime.stop()

    assert inner.stopped
