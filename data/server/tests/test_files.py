"""The Files API against real object storage (throwaway SeaweedFS): versions, quotas, ownership, audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os

import boto3
import httpx
import pytest
from botocore.client import Config
from botocore.exceptions import ClientError
from conftest import ADMIN, VOLUMES_BUCKET

from loamz.data.client import (
    AsyncDataClient,
    DataClient,
    DataError,
    ForbiddenError,
    InsufficientScopeError,
    InvalidRequestError,
    NotFoundError,
    PreconditionFailedError,
    QuotaExceededError,
)
from loamz.data.server.features.files.service import files_service
from loamz.data.storage.errors import LoamzError, http_status

pytestmark = pytest.mark.integration
MiB = 1024 * 1024
VOL = "main.fs.docs"
SMALL = "main.fs.small"  # carries the quota tests
AGENT = "user-42"


@pytest.fixture(scope="module")
def admin(stack) -> DataClient:
    return stack.client("alice", *ADMIN)


@pytest.fixture(scope="module", autouse=True)
def env(stack, admin):
    # File workloads are bursty (hundreds of small calls); the fair-use limiter has its own tests.
    limit, stack.settings.rate_limit_per_minute = stack.settings.rate_limit_per_minute, 0
    admin.catalog.create_schema("main", "fs")
    for name in ("docs", "small"):
        admin.catalog.create_volume(f"main.fs.{name}")
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.fs",
            json={"changes": [{"principal": "group:account users", "add": ["USE_SCHEMA"]}]},
        )
        for name in ("docs", "small"):
            for grantee in ("group:tenant-acme", "group:analysts"):
                done = http.patch(
                    f"/grants/volume/main.fs.{name}",
                    json={
                        "changes": [{"principal": grantee, "add": ["READ_VOLUME", "WRITE_VOLUME"]}]
                    },
                )
                assert done.status_code == 200, done.text
    yield stack
    stack.settings.rate_limit_per_minute = limit


@pytest.fixture(scope="module")
def agent(env) -> DataClient:
    return env.agent_client(AGENT, "acme", scopes=("files:write",))


@pytest.fixture(scope="module")
def bob(env) -> DataClient:
    return env.client("bob", "analysts")


def storage(settings, access=None, secret=None):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=access or settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=secret or settings.s3_secret_access_key.get_secret_value(),
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


# ------------------------------------------------------------------ uploads and downloads
def test_a_small_file_round_trips_with_metadata_and_a_verified_checksum(admin):
    data = b"hello governed files\n" * 100
    info = admin.files.upload(VOL, "basics/hello.txt", data)
    assert info.path == "basics/hello.txt" and info.size == len(data) and info.version == 1
    assert info.content_type == "text/plain" and info.etag == '"1"'
    assert info.sha256 == hashlib.sha256(data).hexdigest() and info.sha256_verified
    assert admin.files.read(VOL, "basics/hello.txt") == data
    assert admin.files.info(VOL, "basics/hello.txt") == info
    assert admin.files.exists(VOL, "basics/hello.txt")
    assert not admin.files.exists(VOL, "basics/missing.txt")
    with pytest.raises(NotFoundError):
        admin.files.read(VOL, "basics/missing.txt")


def test_uploads_come_from_bytes_paths_and_streams(admin, tmp_path):
    (tmp_path / "local.bin").write_bytes(b"from disk" * 1000)
    admin.files.upload(VOL, "sources/disk.bin", tmp_path / "local.bin")
    with (tmp_path / "local.bin").open("rb") as stream:
        admin.files.upload(VOL, "sources/stream.bin", stream)
    admin.files.upload(VOL, "sources/chunks.bin", iter([b"ab", b"cd", b"ef"]))
    assert admin.files.read(VOL, "sources/disk.bin") == b"from disk" * 1000
    assert admin.files.read(VOL, "sources/stream.bin") == b"from disk" * 1000
    assert admin.files.read(VOL, "sources/chunks.bin") == b"abcdef"
    target = admin.files.download(VOL, "sources/disk.bin", tmp_path / "back.bin")
    assert target.read_bytes() == b"from disk" * 1000


def test_a_large_file_uploads_in_parallel_parts_and_downloads_intact(admin, tmp_path):
    big = tmp_path / "big.bin"
    size = 70 * MiB + 123  # over the 64 MiB single-request limit: multipart
    block = os.urandom(MiB)
    with big.open("wb") as out:
        for _ in range(70):
            out.write(block)
        out.write(os.urandom(123))
    started = admin.files.initiate_upload(VOL, "large/big.bin", size)
    assert started["method"] == "MULTIPART" and len(started["parts"]) == 5
    assert sum(p["length"] for p in started["parts"]) == size
    info = admin.files.upload(VOL, "large/big.bin", big, parallelism=4)
    assert info.size == size and info.version == 1
    digest = hashlib.sha256()
    for chunk in admin.files.iter_bytes(VOL, "large/big.bin"):
        digest.update(chunk)
    expected = hashlib.sha256()
    with big.open("rb") as stream:
        while chunk := stream.read(MiB):
            expected.update(chunk)
    assert digest.hexdigest() == expected.hexdigest()


def test_the_bytes_that_arrive_must_match_what_was_declared(env, admin):
    # The signed request pins the length, so the store refuses a body of another size...
    short = admin.files.initiate_upload(VOL, "checks/short.bin", 10)
    with pytest.raises(ForbiddenError):
        admin.files.put_to_url(short["url"], b"12345", short["headers"])
    # ...and if the object is replaced behind the API's back, completing still notices.
    swapped = admin.files.initiate_upload(VOL, "checks/swapped.bin", 5)
    admin.files.put_to_url(swapped["url"], b"12345", swapped["headers"])
    key = env.app.state.container.get(files_service).catalog.upload(swapped["upload_id"])[
        "object_key"
    ]
    storage(env.settings).put_object(Bucket=VOLUMES_BUCKET, Key=key, Body=b"123456789")
    with pytest.raises(DataError) as mismatch:
        admin.files.complete_upload(VOL, swapped["upload_id"])
    assert http_status(mismatch.value) == 409 and mismatch.value.code == "conflict"
    assert not admin.files.exists(VOL, "checks/swapped.bin")
    # An upload never sent is incomplete, and can be completed once it arrives.
    late = admin.files.initiate_upload(VOL, "checks/late.bin", 3)
    with pytest.raises(DataError) as missing:
        admin.files.complete_upload(VOL, late["upload_id"])
    assert missing.value.code == "upload-incomplete"
    admin.files.put_to_url(late["url"], b"abc", late["headers"])
    assert admin.files.complete_upload(VOL, late["upload_id"]).size == 3
    # A wrong SHA-256 is refused by the object store itself, before anything is stored.
    wrong = admin.files.initiate_upload(VOL, "checks/wrong.bin", 5, sha256="0" * 64)
    with pytest.raises(DataError) as refused:
        admin.files.put_to_url(wrong["url"], b"12345", wrong["headers"])
    assert http_status(refused.value) == 400
    with pytest.raises(NotFoundError):
        admin.files.complete_upload(VOL, "0" * 32)


def test_completing_twice_is_harmless_and_only_the_starter_may_complete(env, admin, bob):
    started = admin.files.initiate_upload(VOL, "once/a.txt", 3)
    admin.files.put_to_url(started["url"], b"abc", started["headers"])
    first = admin.files.complete_upload(VOL, started["upload_id"])
    again = admin.files.complete_upload(VOL, started["upload_id"])
    assert first == again and first.version == 1
    other = admin.files.initiate_upload(VOL, "once/b.txt", 3)
    admin.files.put_to_url(other["url"], b"abc", other["headers"])
    with pytest.raises(NotFoundError):
        bob.files.complete_upload(VOL, other["upload_id"])


# ------------------------------------------------------------------ versions, restore, compare-and-swap
def test_every_version_is_kept_and_any_can_be_restored(admin):
    path = "versions/note.txt"
    for text in (b"one", b"two!", b"three!!"):
        admin.files.upload(VOL, path, text)
    versions = list(admin.files.versions(VOL, path))
    assert [v.version for v in versions] == [3, 2, 1]
    assert [v.size for v in versions] == [7, 4, 3] and versions[0].current
    assert admin.files.read(VOL, path, version=1) == b"one"
    restored = admin.files.restore(VOL, path, 1)
    assert restored.version == 4 and admin.files.read(VOL, path) == b"one"
    assert [v.current for v in admin.files.versions(VOL, path)][0] is True
    # Deleting hides the file but keeps its versions; restore without a version undeletes it.
    assert admin.files.delete(VOL, path)
    assert not admin.files.exists(VOL, path) and path not in {
        f.path for f in admin.files.list(VOL, "versions/")
    }
    assert len(list(admin.files.versions(VOL, path))) == 4
    undeleted = admin.files.restore(VOL, path)
    assert undeleted.version == 4 and admin.files.read(VOL, path) == b"one"
    with pytest.raises(NotFoundError):
        admin.files.restore(VOL, path, 99)


def test_writes_can_be_conditional_on_what_the_writer_last_saw(admin):
    path = "cas/lock.txt"
    first = admin.files.upload(VOL, path, b"mine", if_none_match=True)
    with pytest.raises(PreconditionFailedError):
        admin.files.upload(VOL, path, b"yours", if_none_match=True)  # create-only: it exists
    second = admin.files.upload(VOL, path, b"v2", if_match=first)
    assert second.version == 2
    with pytest.raises(PreconditionFailedError):
        admin.files.upload(VOL, path, b"stale", if_match=first)  # based on version 1
    with pytest.raises(PreconditionFailedError):
        admin.files.delete(VOL, path, if_match=first)
    with pytest.raises(PreconditionFailedError):
        admin.files.upload(VOL, "cas/absent.txt", b"x", if_match=1)  # nothing to match
    assert admin.files.read(VOL, path) == b"v2"
    assert admin.files.delete(VOL, path, if_match=second)
    admin.files.upload(VOL, path, b"again", if_none_match=True)  # a deleted path is free again


# ------------------------------------------------------------------ listing, delete, copy
def test_listings_are_prefix_scoped_ordered_paged_and_can_fold_folders(admin):
    for name in ("a/1.txt", "a/2.txt", "a/sub/3.txt", "b/4.txt", "top.txt"):
        admin.files.upload(VOL, "ls/" + name, name.encode())
    everything = [f.path for f in admin.files.list(VOL, "ls/")]
    assert everything == ["ls/a/1.txt", "ls/a/2.txt", "ls/a/sub/3.txt", "ls/b/4.txt", "ls/top.txt"]
    assert [f.path for f in admin.files.list(VOL, "ls/a/")] == [
        "ls/a/1.txt",
        "ls/a/2.txt",
        "ls/a/sub/3.txt",
    ]
    assert [f.path for f in admin.files.list(VOL, "ls/a/", page_size=1)] == everything[:3]
    level = admin.files.list_dir(VOL, "ls/")
    assert [f.path for f in level.files] == ["ls/top.txt"] and level.directories == [
        "ls/a/",
        "ls/b/",
    ]
    deeper = admin.files.list_dir(VOL, "ls/a/")
    assert [f.path for f in deeper.files] == ["ls/a/1.txt", "ls/a/2.txt"]
    assert deeper.directories == ["ls/a/sub/"]
    paged = admin.files.list_page(VOL, "ls/", delimiter="/", page_size=2)
    assert paged["next_page_token"] and len(paged["files"]) + len(paged["directories"]) == 2
    assert [f.path for f in admin.files.list(VOL, "ls/A/")] == []  # prefixes are case-sensitive
    usage = admin.files.usage(VOL, "ls/a/")
    assert usage.files == 3 and usage.bytes == len("a/1.txt") + len("a/2.txt") + len("a/sub/3.txt")


def test_delete_and_prefix_delete_hide_files_without_touching_the_neighbours(admin):
    for name in ("x/1", "x/2", "x/sub/3", "xy/4", "y/5"):
        admin.files.upload(VOL, "del/" + name, b"data")
    assert admin.files.delete(VOL, "del/y/5")
    assert not admin.files.delete(VOL, "del/y/5", missing_ok=True)
    with pytest.raises(NotFoundError):
        admin.files.delete(VOL, "del/y/5")
    done = admin.files.delete_prefix(VOL, "del/x/")
    assert done == {"deleted": 3, "bytes": 12}
    assert [f.path for f in admin.files.list(VOL, "del/")] == ["del/xy/4"]
    assert admin.files.delete_prefix(VOL, "del/zzz/") == {"deleted": 0, "bytes": 0}
    with pytest.raises(InvalidRequestError):
        admin.request("POST", f"/volumes/{VOL}/files:delete-prefix", json={"prefix": "/"})


def test_copy_prefix_forks_a_folder_and_the_copies_are_independent(admin):
    for name in ("main/a.txt", "main/deep/b.txt"):
        admin.files.upload(VOL, "fork/" + name, name.encode())
    done = admin.files.copy_prefix(VOL, "fork/main/", "fork/branch/")
    assert done == {"copied": 2, "skipped": 0, "bytes": len("main/a.txt") + len("main/deep/b.txt")}
    assert admin.files.read(VOL, "fork/branch/deep/b.txt") == b"main/deep/b.txt"
    admin.files.upload(VOL, "fork/branch/a.txt", b"changed on the branch")
    assert admin.files.read(VOL, "fork/main/a.txt") == b"main/a.txt"
    admin.files.delete_prefix(VOL, "fork/main/")  # removing the original keeps the copy readable
    assert admin.files.read(VOL, "fork/branch/deep/b.txt") == b"main/deep/b.txt"
    admin.files.upload(VOL, "fork/c/new.txt", b"n")
    keep = admin.files.copy_prefix(VOL, "fork/c/", "fork/branch/", overwrite=False)
    assert keep["copied"] == 1
    admin.files.upload(VOL, "fork/c/a.txt", b"c")
    assert admin.files.copy_prefix(VOL, "fork/c/", "fork/branch/", overwrite=False)["skipped"] == 2
    for bad in (("fork/", "fork/in/"), ("fork/in/", "fork/")):
        with pytest.raises(InvalidRequestError):
            admin.files.copy_prefix(VOL, *bad)


def test_download_urls_can_name_a_version_and_a_lifetime(admin):
    admin.files.upload(VOL, "urls/f.txt", b"first")
    admin.files.upload(VOL, "urls/f.txt", b"second")
    newest = admin.files.download_url(VOL, "urls/f.txt", expires_in=60)
    oldest = admin.files.download_url(VOL, "urls/f.txt", version=1)
    assert newest["version"] == 2 and oldest["version"] == 1
    assert httpx.get(newest["url"]).content == b"second"
    assert httpx.get(oldest["url"]).content == b"first"
    with pytest.raises(NotFoundError):
        admin.files.download_url(VOL, "urls/f.txt", version=7)


# ------------------------------------------------------------------ batches
def test_many_small_files_move_with_batch_signing(admin):
    files = {
        f"batch/f{i:03d}.txt": f"file {i}".encode() for i in range(230)
    }  # > 200: two sign calls
    written = admin.files.put_many(VOL, files)
    assert all(not isinstance(v, DataError) for v in written.values()) and len(written) == 230
    got = admin.files.get_many(VOL, [*files, "batch/missing.txt", "batch/../escape.txt"])
    assert {p: v for p, v in got.items() if isinstance(v, bytes)} == files
    assert isinstance(got["batch/missing.txt"], NotFoundError)
    assert (
        got["batch/../escape.txt"].code == "invalid-request"
    )  # one bad item does not fail the batch


# ------------------------------------------------------------------ quotas
def test_a_hard_quota_refuses_writes_that_would_pass_it(env, admin, bob):
    admin.files.set_quota(SMALL, 1000)
    admin.files.upload(SMALL, "q/a.bin", b"x" * 600)
    with pytest.raises(QuotaExceededError) as over:
        admin.files.upload(SMALL, "q/b.bin", b"x" * 600)
    assert (
        http_status(over.value) == 413
        and over.value.quota_bytes == 1000
        and over.value.used_bytes == 600
    )
    assert not admin.files.exists(SMALL, "q/b.bin")
    admin.files.upload(SMALL, "q/a.bin", b"y" * 950)  # replacing counts the difference only
    usage = admin.files.usage(SMALL)
    assert usage.bytes == 950 and usage.quota_bytes == 1000 and usage.retained_bytes == 600
    admin.files.delete(SMALL, "q/a.bin")
    assert admin.files.usage(SMALL).bytes == 0
    admin.files.upload(SMALL, "q/b.bin", b"x" * 1000)  # exactly at the quota is allowed
    # copies and restores count too
    with pytest.raises(QuotaExceededError):
        admin.files.copy_prefix(SMALL, "q/", "q2/")
    assert admin.files.usage(SMALL, "q2/").files == 0  # nothing half-copied
    with pytest.raises(QuotaExceededError):
        admin.files.restore(SMALL, "q/a.bin")
    # Only the owner (or an admin) sets quotas.
    with pytest.raises(ForbiddenError):
        bob.files.set_quota(SMALL, 10**12)
    admin.files.set_quota(SMALL, None)
    assert admin.files.usage(SMALL).quota_bytes is None
    admin.files.upload(SMALL, "q/big.bin", b"x" * 5000)


# ------------------------------------------------------------------ paths
@pytest.mark.parametrize(
    "path",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "/etc/passwd",
        "a\\..\\b.txt",
        "a/./b.txt",
        "x\x00y",
        "d/",
        "versions",
    ],
)
def test_traversal_and_malformed_paths_are_refused_everywhere(admin, path):
    for call in (
        lambda: admin.files.initiate_upload(VOL, path, 1),
        lambda: admin.files.info(VOL, path),
        lambda: admin.files.delete(VOL, path),
        lambda: admin.files.download_url(VOL, path),
        lambda: admin.files.restore(VOL, path),
    ):
        with pytest.raises((InvalidRequestError, NotFoundError, DataError)) as refused:
            call()
        assert http_status(refused.value) in {400, 404}
    for prefix in ("../", "/", "a\\b"):
        with pytest.raises(DataError):
            list(admin.files.list(VOL, prefix))
        with pytest.raises(DataError):
            admin.files.copy_prefix(VOL, prefix, "ok/")


def test_encoded_traversal_in_the_url_never_reaches_another_path(env, admin):
    admin.files.upload(VOL, "safe/inside.txt", b"inside")
    with env.http("alice", *ADMIN) as http:
        for raw in (
            "safe/%2e%2e/%2e%2e/etc/passwd",
            "%2e%2e/%2e%2e/x",
            "safe%2f..%2f..%2fx",
            "..%5c..%5cx",
        ):
            answer = http.get(f"/volumes/{VOL}/files/{raw}")
            assert answer.status_code in {400, 404}, (raw, answer.text)


# ------------------------------------------------------------------ ownership for delegated tokens
def test_a_delegated_token_reaches_only_its_own_users_folder(env, admin, agent):
    admin.files.upload(VOL, f"users/{AGENT}/mine.txt", b"mine")
    admin.files.upload(VOL, "users/someone-else/theirs.txt", b"theirs")
    admin.files.upload(VOL, "shared/readme.txt", b"shared")
    agent.files.upload(VOL, f"users/{AGENT}/note.txt", b"agent wrote this")
    assert agent.files.read(VOL, f"users/{AGENT}/note.txt") == b"agent wrote this"
    assert agent.files.read(VOL, "shared/readme.txt") == b"shared"  # outside users/: grants decide
    for call in (
        lambda: agent.files.read(VOL, "users/someone-else/theirs.txt"),
        lambda: agent.files.info(VOL, "users/someone-else/theirs.txt"),
        lambda: agent.files.upload(VOL, "users/someone-else/planted.txt", b"x"),
        lambda: agent.files.delete(VOL, "users/someone-else/theirs.txt"),
        lambda: list(agent.files.list(VOL, "users/someone-else/")),
        lambda: agent.files.delete_prefix(VOL, "users/"),
        lambda: agent.files.copy_prefix(VOL, f"users/{AGENT}/", "users/someone-else/loot/"),
        lambda: agent.files.usage(VOL, "users/someone-else/"),
    ):
        with pytest.raises(ForbiddenError) as refused:
            call()
        assert refused.value.code == "forbidden"
    seen = {f.path for f in agent.files.list(VOL, "users/")}
    assert f"users/{AGENT}/note.txt" in seen and not any("someone-else" in p for p in seen)
    folders = agent.files.list_dir(VOL, "users/").directories
    assert folders == [f"users/{AGENT}/"]
    results = agent.files.sign(
        VOL,
        [
            {"path": f"users/{AGENT}/note.txt", "action": "get"},
            {"path": "users/someone-else/theirs.txt", "action": "get"},
            {"path": "users/someone-else/planted.txt", "action": "put", "size": 1},
        ],
    )
    assert "url" in results[0]
    assert [r["error"]["type"] for r in results[1:]] == ["path-forbidden", "path-forbidden"]
    assert not admin.files.exists(VOL, "users/someone-else/planted.txt")
    # Ordinary users (no agent in between) and admins follow grants alone.
    assert admin.files.read(VOL, "users/someone-else/theirs.txt") == b"theirs"


def test_scopes_decide_which_routes_a_delegated_token_may_use(env, admin, agent):
    read_only_run = env.agent_client(AGENT, "acme")  # the default scope: data:read
    with pytest.raises(ForbiddenError) as no_write:
        read_only_run.files.upload(VOL, f"users/{AGENT}/x.txt", b"x")
    assert no_write.value.code == "forbidden"
    with pytest.raises(InsufficientScopeError):
        list(read_only_run.files.list(VOL, ""))  # reads need files:read, not just data:read
    reader = env.agent_client(AGENT, "acme", scopes=("files:read",))
    assert [f.path for f in reader.files.list(VOL, f"users/{AGENT}/")]
    assert reader.files.read(VOL, "shared/readme.txt") == b"shared"
    with pytest.raises(InsufficientScopeError) as needs_write:
        reader.files.upload(VOL, f"users/{AGENT}/y.txt", b"y")
    assert needs_write.value.required == ["files:write"]
    with pytest.raises(InsufficientScopeError):
        reader.files.sign(VOL, [{"path": f"users/{AGENT}/y.txt", "action": "put", "size": 1}])
    # Everything else stays closed to a files:write token: it holds no other route's scope.
    closed = [
        ("POST", "/catalogs", {"name": "mine"}),
        ("POST", "/catalogs/main/schemas", {"name": "mine"}),
        (
            "PATCH",
            "/grants/volume/main.fs.docs",
            {"changes": [{"principal": "user:x", "add": ["READ_VOLUME"]}]},
        ),
        ("PUT", f"/volumes/{VOL}/quotas", {"max_bytes": 1}),
        ("POST", "/tenants", {"tenant_id": "t", "alias": "t"}),
        ("POST", "/imports", {"table": "main.fs.t", "files": [{"name": "a.csv", "size": 1}]}),
        ("DELETE", "/catalogs/main/schemas/fs/volumes/docs", None),
    ]
    for method, path, body in closed:
        with pytest.raises(InsufficientScopeError):
            agent.request(method, path, **({"json": body} if body else {}))
    assert admin.files.usage(VOL).quota_bytes is None  # the quota call really was refused


# ------------------------------------------------------------------ storage isolation and audit
def test_compute_workers_hold_no_access_to_the_volumes_bucket(env, admin):
    admin.files.upload(VOL, "isolation/secret.txt", b"private user data")
    key = admin.files.download_url(VOL, "isolation/secret.txt")["url"]
    assert "isolation" not in key  # object keys never carry file names
    settings = env.worker_settings
    worker = storage(
        settings,
        settings.s3_access_key_id.get_secret_value(),
        settings.s3_secret_access_key.get_secret_value(),
    )
    control = storage(env.settings)
    listed = control.list_objects_v2(Bucket=VOLUMES_BUCKET, Prefix="volumes/")["Contents"]
    assert listed and all(
        o["Key"].startswith("volumes/") and "/objects/" in o["Key"] for o in listed
    )
    with pytest.raises(ClientError) as listing:
        worker.list_objects_v2(Bucket=VOLUMES_BUCKET)
    assert listing.value.response["Error"]["Code"] in {"AccessDenied", "403"}
    with pytest.raises(ClientError) as reading:
        worker.get_object(Bucket=VOLUMES_BUCKET, Key=listed[0]["Key"])
    assert reading.value.response["Error"]["Code"] in {"AccessDenied", "403"}
    with pytest.raises(ClientError):
        worker.put_object(Bucket=VOLUMES_BUCKET, Key="volumes/planted", Body=b"x")
    worker.list_objects_v2(
        Bucket=env.settings.staging_bucket
    )  # the worker's own buckets still work


def test_the_audit_log_records_ids_and_hashes_never_file_names(env, admin, agent):
    marker = "very-recognisable-name-9d2f"
    info = admin.files.upload(VOL, f"audit/{marker}.txt", b"audited")
    admin.files.copy_prefix(VOL, "audit/", f"audit-copy/{marker}/")
    admin.files.sign(VOL, [{"path": f"audit/{marker}.txt", "action": "get"}])
    admin.files.download_url(VOL, f"audit/{marker}.txt")
    admin.files.restore(VOL, f"audit/{marker}.txt", 1)
    admin.files.delete(VOL, f"audit/{marker}.txt")
    admin.files.delete_prefix(VOL, f"audit-copy/{marker}/")
    store = env.app.state.container.store
    events = store.audit_events(None, 500)
    ours = [
        e for e in events if e["action"].startswith(("file.", "volume.quota", "upload_session"))
    ]
    actions = {e["action"] for e in ours}
    assert {
        "file.upload",
        "file.copy_prefix",
        "file.sign",
        "file.download_url",
        "file.restore",
        "file.delete",
        "file.delete_prefix",
    } <= actions
    text = json.dumps(events)
    assert marker not in text and f"audit/{marker}" not in text
    upload = next(
        e
        for e in ours
        if e["action"] == "file.upload" and e["detail"].get("size") == len(b"audited")
    )
    assert upload["detail"]["file_id"] and upload["detail"]["version"] == info.version
    assert (
        upload["detail"]["path_hash"]
        == hashlib.sha256(f"audit/{marker}.txt".encode()).hexdigest()[:16]
    )
    assert upload["principal"] == "user:alice" and upload["securable"] == VOL
    agent.files.upload(VOL, f"users/{AGENT}/audited.txt", b"by an agent")
    by_agent = next(e for e in store.audit_events(None, 50) if e["action"] == "file.upload")
    assert by_agent["actor"] == "loamz-agents:primary"
    assert store.verify_audit()["ok"] is True


# ------------------------------------------------------------------ upload sessions, sweeping
def test_an_upload_session_is_promoted_into_a_volume_by_a_server_side_copy(env, admin, bob):
    started = admin.files.create_session(11, content_type="text/plain")
    assert started["method"] == "PUT"
    admin.files.put_to_url(started["url"], b"hello world", started["headers"])
    with pytest.raises(NotFoundError):
        bob.files.promote_session(started["id"], VOL, "sessions/a.txt")  # someone else's session
    info = admin.files.promote_session(started["id"], VOL, "sessions/a.txt", if_none_match=True)
    assert info.size == 11 and admin.files.read(VOL, "sessions/a.txt") == b"hello world"
    with pytest.raises(DataError) as spent:
        admin.files.promote_session(started["id"], VOL, "sessions/again.txt")
    assert http_status(spent.value) == 409
    # Promotion respects quotas and grants of the target volume.
    admin.files.set_quota(SMALL, 5)
    pending = admin.files.create_session(11)
    admin.files.put_to_url(pending["url"], b"hello world", pending["headers"])
    with pytest.raises(QuotaExceededError):
        admin.files.promote_session(pending["id"], SMALL, "promoted.txt")
    admin.files.set_quota(SMALL, None)


def test_the_sweeper_expires_pending_uploads_and_purges_superseded_versions(env, admin):
    files = env.app.state.container.get(files_service)
    store = env.app.state.container.store
    s3 = storage(env.settings)
    # 1. A pending upload past its expiry loses its object and its row.
    abandoned = admin.files.initiate_upload(VOL, "sweep/abandoned.bin", 4)
    admin.files.put_to_url(abandoned["url"], b"data", abandoned["headers"])
    key = files.catalog.upload(abandoned["upload_id"])["object_key"]
    assert s3.head_object(Bucket=VOLUMES_BUCKET, Key=key)
    with store.connection() as db:
        db.execute(
            "UPDATE volume_uploads SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (abandoned["upload_id"],),
        )
    assert files.sweep()["uploads"] >= 1
    with pytest.raises(ClientError):
        s3.head_object(Bucket=VOLUMES_BUCKET, Key=key)
    with pytest.raises(LoamzError):
        files.catalog.upload(abandoned["upload_id"])
    # 2. Versions superseded long ago are purged, their objects removed; the current one survives.
    admin.files.upload(VOL, "sweep/old.txt", b"old")
    admin.files.upload(VOL, "sweep/old.txt", b"new")
    old_key = files.catalog.current_object(_volume_id(files), "sweep/old.txt", 1)["object_key"]
    with store.connection() as db:
        db.execute(
            "UPDATE volume_file_versions SET created_at='2000-01-01T00:00:00+00:00' WHERE object_key=?",
            (old_key,),
        )
        db.execute(
            "UPDATE volume_file_versions SET created_at='2000-01-02T00:00:00+00:00' "
            "WHERE version=2 AND file_id=(SELECT file_id FROM volume_file_versions WHERE object_key=?)",
            (old_key,),
        )
    assert files.sweep()["versions"] >= 1
    with pytest.raises(ClientError):
        s3.head_object(Bucket=VOLUMES_BUCKET, Key=old_key)
    assert admin.files.read(VOL, "sweep/old.txt") == b"new"
    purged = {v.version: v.purged for v in admin.files.versions(VOL, "sweep/old.txt")}
    assert purged == {2: False, 1: True}
    with pytest.raises(NotFoundError):
        admin.files.read(VOL, "sweep/old.txt", version=1)


def _volume_id(files) -> str:
    with files.store.connection() as db:
        return db.execute(
            "SELECT a.id FROM catalog_assets a WHERE a.kind='volume' AND a.name='docs'"
        ).fetchone()["id"]


def test_files_of_an_unregistered_volume_are_removed_by_the_sweeper(env, admin):
    admin.catalog.create_volume("main.fs.temporary")
    admin.files.upload("main.fs.temporary", "gone.txt", b"bye")
    files = env.app.state.container.get(files_service)
    with files.store.connection() as db:
        volume_id = db.execute("SELECT id FROM catalog_assets WHERE name='temporary'").fetchone()[
            "id"
        ]
    s3 = storage(env.settings)
    prefix = f"volumes/{volume_id}/"
    assert s3.list_objects_v2(Bucket=VOLUMES_BUCKET, Prefix=prefix)["KeyCount"] == 1
    admin.request("DELETE", "/catalogs/main/schemas/fs/volumes/temporary")
    assert files.sweep()["volumes"] >= 1
    assert s3.list_objects_v2(Bucket=VOLUMES_BUCKET, Prefix=prefix)["KeyCount"] == 0


# ------------------------------------------------------------------ the asyncio client
def test_the_async_client_does_the_same_things_concurrently(env):
    async def scenario() -> None:
        async with AsyncDataClient(env.api, token=lambda: env.token("alice", *ADMIN)) as client:
            files = client.files
            await asyncio.gather(
                *(files.upload(VOL, f"aio/f{i}.txt", f"async {i}".encode()) for i in range(12))
            )
            listed = [f.path async for f in files.list(VOL, "aio/")]
            assert listed == sorted(f"aio/f{i}.txt" for i in range(12))
            assert await files.read(VOL, "aio/f3.txt") == b"async 3"
            many = await files.get_many(VOL, listed)
            assert many["aio/f11.txt"] == b"async 11"
            written = await files.put_many(VOL, {"aio/batch/a": b"a", "aio/batch/b": b"b"})
            assert all(not isinstance(v, DataError) for v in written.values())
            with pytest.raises(PreconditionFailedError):
                await files.upload(VOL, "aio/f3.txt", b"x", if_none_match=True)
            big = os.urandom(70 * MiB)
            info = await files.upload(VOL, "aio/big.bin", big, parallelism=3)
            assert info.size == len(big)
            digest = hashlib.sha256()
            async for chunk in files.iter_bytes(VOL, "aio/big.bin"):
                digest.update(chunk)
            assert digest.hexdigest() == hashlib.sha256(big).hexdigest()
            usage = await files.usage(VOL, "aio/")
            assert usage.files == 12 + 2 + 1
            assert [v.version async for v in files.versions(VOL, "aio/f3.txt")] == [1]
            await files.delete_prefix(VOL, "aio/")

    asyncio.run(scenario())
