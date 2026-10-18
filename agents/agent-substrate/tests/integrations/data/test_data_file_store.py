"""``DataFileStore`` against a real substrate-data: the ``FileStore`` conformance suite, isolation, errors and large files."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator

import pytest

from substrate.integrations.data import (
    DataFilesConnection,
    DataFileStore,
    DataUploadSessions,
)
from substrate.stores.files import WorkspacePathError, WorkspaceQuotaExceededError
from substrate.testing.conformance.file_store import FileStoreConformance
from substrate.types import RunScope
from substrate.types.errors import AuthError, ObjectNotFoundError, ScopeViolationError

from .conftest import DataService

pytestmark = pytest.mark.integration

MiB = 1024 * 1024


def connection(
    service: DataService,
    tenants: tuple[str, ...] = ("t1",),
    scopes: tuple[str, ...] = ("files:write",),
):
    return DataFilesConnection(
        service.url,
        tokens=lambda scope: service.token_for(scope.user_id or "", tenants, scopes),
    )


def scope(user: str, tenant: str = "t1") -> RunScope:
    return RunScope(tenant_id=tenant, user_id=user)


@pytest.fixture
async def files(data_service: DataService) -> AsyncIterator[DataFilesConnection]:
    conn = connection(data_service, ("t1", "t2"))
    yield conn
    await conn.aclose()


class TestDataFileStore(FileStoreConformance):
    """The very suite every other FileStore runs, unchanged. The caller is user ``u1`` of tenants ``t1`` and ``t2``."""

    @pytest.fixture
    async def store(self, files: DataFilesConnection) -> AsyncIterator[DataFileStore]:
        store = files.store(scope("u1"))
        yield store
        for tenant in ("t1", "t2"):
            await store.delete_prefix(f"tenants/{tenant}/users/u1/")
            await store.delete_prefix(f"tenants/{tenant}/conversations/")


async def test_another_users_files_do_not_exist_for_you(
    data_service: DataService,
) -> None:
    async with _closing(connection(data_service)) as conn:
        alice, bob = conn.store(scope("alice")), conn.store(scope("bob"))
        key = "tenants/t1/users/alice/secret.txt"
        await alice.upload(key, b"alice only")
        assert await bob.exists(key) is False
        with pytest.raises(ObjectNotFoundError):
            await bob.download(key)
        with pytest.raises(ObjectNotFoundError):
            await bob.presign_url(key)
        with pytest.raises(ObjectNotFoundError):
            await bob.info(key)
        assert await bob.list_prefix("tenants/t1/users/alice/") == []
        assert await bob.delete_prefix("tenants/t1/users/alice/") == 0
        assert await alice.download(key) == b"alice only"
        with pytest.raises(ScopeViolationError):
            await bob.upload(key, b"overwrite")
        assert await alice.download(key) == b"alice only"
        await alice.delete(key)


async def test_another_tenants_files_do_not_exist_for_you(
    data_service: DataService,
) -> None:
    async with (
        _closing(connection(data_service, ("t1", "t2"))) as both,
        _closing(connection(data_service)) as only_t1,
    ):
        owner, outsider = (
            both.store(scope("carol", "t2")),
            only_t1.store(scope("carol")),
        )
        key = "tenants/t2/users/carol/plan.txt"
        await owner.upload(key, b"tenant two")
        assert await outsider.exists(key) is False
        with pytest.raises(ObjectNotFoundError):
            await outsider.download(key)
        assert await outsider.list_prefix("tenants/t2/") == []
        assert await outsider.usage_bytes("t2") == 0
        await owner.delete(key)


async def test_hostile_and_malformed_keys_are_refused_before_any_call(
    files: DataFilesConnection,
) -> None:
    store = files.store(scope("u1"))
    for bad in (
        "etc/passwd",
        "tenants/",
        "tenants//x",
        "tenants/t1/../t2/x",
        "tenants/t1/users/u1/a\\b",
        "tenants/t1/",
    ):
        with pytest.raises(WorkspacePathError):
            await store.upload(bad, b"x")
    with pytest.raises(WorkspacePathError):
        await store.delete_prefix("tenants/t1/")  # never a whole volume
    with pytest.raises(WorkspacePathError):
        await store.copy_prefix("tenants/t1/users/u1/a/", "tenants/t2/users/u1/a/")


async def test_a_token_without_the_scope_is_an_auth_error_not_a_crash(
    data_service: DataService,
) -> None:
    async with _closing(connection(data_service, scopes=("data:read",))) as conn:
        store = conn.store(scope("u1"))
        with pytest.raises(AuthError):
            await store.upload("tenants/t1/users/u1/x", b"x")


async def test_quota_exceeded_maps_to_the_engines_error(
    data_service: DataService,
) -> None:
    from substrate_data_client import DataClient

    admin = data_service.admin()
    admin.tenants.ensure("tq", "tq")
    admin.files.set_quota("tenant_tq.workspace.files", 100)
    async with _closing(connection(data_service, ("tq",))) as conn:
        store = conn.store(scope("u1", "tq"))
        await store.upload("tenants/tq/users/u1/small", b"x" * 60)
        with pytest.raises(WorkspaceQuotaExceededError) as caught:
            await store.upload("tenants/tq/users/u1/big", b"x" * 80)
        assert caught.value.quota_bytes == 100 and caught.value.tenant_id == "tq"
        assert await store.exists("tenants/tq/users/u1/big") is False
        assert await store.usage_bytes("tq") == 60
    admin.close()
    assert DataClient  # quiet linters about the import used for typing only


async def test_a_large_file_goes_multipart_and_comes_back_intact(
    files: DataFilesConnection,
) -> None:
    store = files.store(scope("u1"))
    data = os.urandom(
        70 * MiB + 123
    )  # over the single-request limit: parallel parts straight to storage
    key = "tenants/t1/users/u1/large/big.bin"
    await store.upload(key, data)
    info = await store.info(key)
    assert info.size == len(data) and info.sha256 == hashlib.sha256(data).hexdigest()
    assert await store.download(key) == data
    await store.delete(key)


async def test_many_small_files_use_batch_signing_and_report_each_outcome(
    files: DataFilesConnection,
) -> None:
    store = files.store(scope("u1"))
    batch = {
        f"tenants/t1/users/u1/many/{i}.txt": f"file {i}".encode() for i in range(250)
    }  # over one 200-item signing call
    batch["tenants/t1/users/other/denied.txt"] = b"not yours"
    batch["nonsense"] = b"bad key"
    results = await store.upload_many(batch)
    assert isinstance(results["nonsense"], WorkspacePathError)
    assert isinstance(results["tenants/t1/users/other/denied.txt"], BaseException)
    assert all(v is None for k, v in results.items() if "/many/" in k)
    keys = [k for k in batch if "/many/" in k]
    got = await store.download_many([*keys, "tenants/t1/users/u1/many/absent.txt"])
    assert got["tenants/t1/users/u1/many/7.txt"] == b"file 7"
    assert isinstance(got["tenants/t1/users/u1/many/absent.txt"], ObjectNotFoundError)
    assert await store.delete_prefix("tenants/t1/users/u1/many/") == 250


async def test_versions_restore_and_direct_uploads(files: DataFilesConnection) -> None:
    store = files.store(scope("u1"))
    key = "tenants/t1/users/u1/doc.txt"
    await store.upload(key, b"one")
    await store.upload(key, b"two")
    versions = await store.versions(key)
    assert [v.version for v in versions] == [2, 1] and versions[0].current
    await store.restore(key, 1)
    assert await store.download(key) == b"one"
    assert await store.read_version(key, 2) == b"two"
    # A direct upload: this process only signs; the "browser" sends the bytes.
    payload = b"direct bytes"
    ticket = await store.initiate_upload(
        "tenants/t1/users/u1/direct.bin",
        len(payload),
        content_type="application/x-test",
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert ticket.method == "PUT" and ticket.url
    await files.client.files.put_to_url(ticket.url, payload, ticket.headers)
    done = await store.complete_upload(ticket.key, ticket.upload_id)
    assert done.size == len(payload) and await store.download(ticket.key) == payload
    await store.delete_prefix("tenants/t1/users/u1/")


async def test_upload_sessions_promote_into_the_users_own_folder(
    files: DataFilesConnection,
) -> None:
    store, sessions = files.store(scope("u1")), DataUploadSessions(files, scope("u1"))
    payload = b"attached in the composer"
    ticket = await sessions.create(len(payload), content_type="text/plain")
    assert await sessions.put(ticket, payload) is None
    info = await sessions.promote(
        ticket.upload_id, "tenants/t1/users/u1/attachments/a.txt"
    )
    assert info.size == len(payload)
    assert await store.download("tenants/t1/users/u1/attachments/a.txt") == payload
    other = DataUploadSessions(files, scope("u1"))
    second = await other.create(3)
    await other.put(second, b"abc")
    with pytest.raises(ScopeViolationError):  # promoting into somebody else's folder
        await other.promote(second.upload_id, "tenants/t1/users/someone-else/a.txt")
    await store.delete_prefix("tenants/t1/users/u1/")


async def test_the_sandbox_workspace_round_trips_through_blob_cas(
    files: DataFilesConnection, tmp_path
) -> None:
    """StagedSandboxRuntime with DataFileStore as its object store: stage a branch in, run, commit the new files out, stage in again."""
    from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.base import (
        ExecResult,
        SandboxSpec,
    )
    from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.staged import (
        StagedSandboxRuntime,
    )
    from substrate.stores import Store
    from substrate.workspace import BlobCAS, WorkspaceScope, Workspaces
    from substrate.workspace.materialize import commit

    store = files.store(scope("u1"))
    workspaces = Workspaces(Store.at(tmp_path / "ws"))
    scratch = tmp_path / "scratch"
    seen: dict[str, bytes] = {}

    class Inner:
        name = "fake"

        async def execute(self, spec: SandboxSpec) -> ExecResult:
            session = scratch / spec.session_dir
            seen.clear()
            seen.update(
                {
                    p.relative_to(session).as_posix(): p.read_bytes()
                    for p in session.rglob("*")
                    if p.is_file()
                }
            )
            (session / "result.bin").write_bytes(b"\x00result" * 1000)
            return ExecResult(stdout="ran", output_files=[])

        async def stop(self) -> None: ...

    spec = SandboxSpec(
        user_id="u1",
        thread_id="conv-1",
        session_dir="conv-1/main",
        code="x=1",
        extra={
            "workspace_scope": WorkspaceScope(
                tenant_id="t1", user_id="u1", conversation_id="conv-1", branch_id="main"
            )
        },
    )
    # Seed the branch the way a previous turn would have: blobs in the data service, a snapshot in the workspace store.
    cas = BlobCAS(store, tenant_id="t1", user_id="u1")
    seed = tmp_path / "seed"
    (seed / "sub").mkdir(parents=True)
    (seed / "sub" / "app.py").write_text("print('hello')\n")
    (seed / "data.bin").write_bytes(os.urandom(300_000))
    snapshot = await commit(
        cas, seed, session_id="conv-1", branch_id="main", parent=None
    )
    await workspaces.commit_snapshot(
        "conv-1", "main", snapshot, expected_parent_snapshot_id=None
    )

    runtime = StagedSandboxRuntime(
        Inner(), object_store=store, workspace_store=workspaces, scratch_root=scratch
    )
    await runtime.execute(spec)
    assert seen == {
        "sub/app.py": b"print('hello')\n",
        "data.bin": (seed / "data.bin").read_bytes(),
    }

    # The run's new file was committed through the CAS into the data service; a fresh checkout (no local cache) sees it.
    await runtime.execute(spec)
    assert (
        seen["result.bin"] == b"\x00result" * 1000
        and seen["sub/app.py"] == b"print('hello')\n"
    )
    assert len(await store.list_prefix("tenants/t1/users/u1/blobs/")) >= 3
    assert await store.delete_prefix("tenants/t1/users/u1/blobs/") >= 3


class _closing:
    def __init__(self, conn: DataFilesConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> DataFilesConnection:
        return self.conn

    async def __aexit__(self, *exc: object) -> None:
        await self.conn.aclose()


async def test_a_key_scoped_store_acts_as_the_owner_the_key_names(
    data_service: DataService,
) -> None:
    from substrate.integrations.data import KeyScopedFileStore

    async with _closing(connection(data_service)) as conn:
        shared = KeyScopedFileStore(conn)
        await shared.upload("tenants/t1/users/ann/a.txt", b"ann's")
        await shared.upload("tenants/t1/users/bob/a.txt", b"bob's")
        await shared.upload("tenants/t1/knowledge/kb/doc.txt", b"tenant-wide")
        assert await shared.download("tenants/t1/users/ann/a.txt") == b"ann's"
        # The identity is the key's owner, so the data service sees ann and bob as different subjects:
        ann = conn.store(scope("ann"))
        assert await ann.exists("tenants/t1/users/bob/a.txt") is False
        assert await ann.download("tenants/t1/knowledge/kb/doc.txt") == b"tenant-wide"
        assert [
            k for k, _s, _m in await shared.list_prefix("tenants/t1/users/bob/")
        ] == ["tenants/t1/users/bob/a.txt"]
        for prefix in ("users/ann/", "users/bob/", "knowledge/"):
            await shared.delete_prefix(f"tenants/t1/{prefix}")
