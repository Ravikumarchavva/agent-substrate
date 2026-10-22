"""`client.files` against an in-memory stand-in for the API and the object store (no network)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import threading
import time
from urllib.parse import unquote

import httpx
import pytest

from loamz.data import (
    AsyncDataClient,
    DataClient,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
    QuotaExceededError,
)

V = "main.ws.files"
PART = 1024  # the stand-in splits anything over 1 KiB into 1 KiB parts


def problem(status, slug, **extra):
    return httpx.Response(
        status,
        json={
            "type": f"https://loamz.com/problems/{slug}",
            "status": status,
            "detail": slug,
            **extra,
        },
    )


class FakeService:
    """Speaks just enough of the Files API and of S3 presigned URLs to drive the client."""

    def __init__(self, page=2):
        self.files: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}
        self.store: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self.headers: list[dict] = []
        self.page = page
        self.flaky_puts = 0
        self.in_flight = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.expired = False

    # -- helpers
    def file_json(self, path):
        f = self.files[path]
        return {
            "path": path,
            "size": len(f["data"]),
            "sha256": hashlib.sha256(f["data"]).hexdigest(),
            "sha256_verified": True,
            "content_type": f["content_type"],
            "version": f["version"],
            "etag": f'"{f["version"]}"',
            "created_by": "user:t",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T03:04:05+00:00",
        }

    # -- storage
    def storage(self, request):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(0.02)
            path = request.url.path
            if self.expired:
                return httpx.Response(
                    403,
                    text="<Error><Code>SignatureDoesNotMatch</Code><Message>expired</Message></Error>",
                )
            if request.method == "PUT":
                if self.flaky_puts:
                    self.flaky_puts -= 1
                    return httpx.Response(503, text="<Error><Code>SlowDown</Code></Error>")
                self.store[path] = request.content
                self.headers.append(dict(request.headers))
                return httpx.Response(
                    200, headers={"ETag": f'"{hashlib.md5(request.content).hexdigest()}"'}
                )
            if path.startswith("/file/"):
                name = path.removeprefix("/file/")
                if name in self.files:
                    return httpx.Response(200, content=self.files[name]["data"])
            if path not in self.store:
                return httpx.Response(404, text="<Error><Code>NoSuchKey</Code></Error>")
            return httpx.Response(200, content=self.store[path])
        finally:
            with self.lock:
                self.in_flight -= 1

    # -- API
    def api(self, request):
        path = unquote(request.url.path).removeprefix("/api/v1")
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path))
        self.bodies.append(body)
        match = request.headers.get("if-match")
        none_match = request.headers.get("if-none-match")
        if path.endswith("files:initiate-upload"):
            target = body["path"]
            if target.startswith("over/"):
                return problem(413, "quota-exceeded", used=5, limit=10)
            if none_match == "*" and target in self.files:
                return problem(412, "precondition-failed")
            if match is not None and (
                target not in self.files or f'"{self.files[target]["version"]}"' != match
            ):
                return problem(412, "precondition-failed")
            upload_id = f"u{len(self.pending)}"
            self.pending[upload_id] = {
                "path": target,
                "size": body["size"],
                "type": body.get("content_type"),
            }
            headers = {"Content-Type": body.get("content_type", "application/octet-stream")}
            if body["size"] <= PART:
                return httpx.Response(
                    200,
                    json={
                        "upload_id": upload_id,
                        "method": "PUT",
                        "url": f"http://store.test/obj/{upload_id}",
                        "headers": headers,
                    },
                )
            count = -(-body["size"] // PART)
            return httpx.Response(
                200,
                json={
                    "upload_id": upload_id,
                    "method": "MULTIPART",
                    "part_size": PART,
                    "parts": [
                        {
                            "part_number": n + 1,
                            "offset": n * PART,
                            "length": min(PART, body["size"] - n * PART),
                            "url": f"http://store.test/obj/{upload_id}/{n + 1}",
                        }
                        for n in range(count)
                    ],
                },
            )
        if path.endswith("files:complete-upload"):
            pending = self.pending[body["upload_id"]]
            key = f"/obj/{body['upload_id']}"
            if body["parts"]:
                numbers = [p["part_number"] for p in body["parts"]]
                assert numbers == sorted(numbers)
                data = b"".join(self.store[f"{key}/{n}"] for n in numbers)
                for p in body["parts"]:
                    part = self.store[f"{key}/{p['part_number']}"]
                    assert p["etag"] == hashlib.md5(part).hexdigest()  # quotes stripped
            else:
                data = self.store[key]
            assert len(data) == pending["size"]
            old = self.files.get(pending["path"])
            self.files[pending["path"]] = {
                "data": data,
                "content_type": pending["type"] or "application/octet-stream",
                "version": (old["version"] + 1) if old else 1,
            }
            return httpx.Response(200, json=self.file_json(pending["path"]))
        if path.endswith("files:sign"):
            assert len(body["items"]) <= 200
            out = []
            for item in body["items"]:
                if ".." in item["path"]:
                    out.append(
                        {
                            "path": item["path"],
                            "action": item["action"],
                            "error": {"type": "invalid-path", "status": 400, "detail": "no"},
                        }
                    )
                elif item["action"] == "get":
                    if item["path"] not in self.files:
                        out.append(
                            {
                                "path": item["path"],
                                "action": "get",
                                "error": {"type": "not-found", "status": 404, "detail": "gone"},
                            }
                        )
                    else:
                        out.append(
                            {
                                "path": item["path"],
                                "action": "get",
                                "url": f"http://store.test/file/{item['path']}",
                            }
                        )
                else:
                    upload_id = f"u{len(self.pending)}"
                    self.pending[upload_id] = {
                        "path": item["path"],
                        "size": item["size"],
                        "type": item.get("content_type"),
                    }
                    out.append(
                        {
                            "path": item["path"],
                            "action": "put",
                            "upload_id": upload_id,
                            "url": f"http://store.test/obj/{upload_id}",
                            "headers": {},
                        }
                    )
            return httpx.Response(200, json={"items": out})
        if path.endswith("files") and request.method == "GET":
            prefix = request.url.params.get("prefix", "")
            token = request.url.params.get("page_token", "")
            delimiter = request.url.params.get("delimiter")
            names = sorted(p for p in self.files if p.startswith(prefix) and p > token)
            entries, dirs = [], []
            for name in names:
                rest = name[len(prefix) :]
                if delimiter and "/" in rest:
                    d = prefix + rest.split("/")[0] + "/"
                    if d not in dirs:
                        dirs.append(d)
                else:
                    entries.append(name)
            page = entries[: self.page]
            more = len(entries) > self.page
            return httpx.Response(
                200,
                json={
                    "files": [self.file_json(p) for p in page],
                    "directories": dirs if not more else [],
                    "next_page_token": page[-1] if more else None,
                },
            )
        if path.endswith(":download-url"):
            name = path.split("/files/", 1)[1].removesuffix(":download-url")
            if name not in self.files:
                return problem(404, "not-found")
            return httpx.Response(
                200, json={"url": f"http://store.test/file/{name}", "version": 1, "size": 1}
            )
        if "/files/" in path and request.method == "GET":
            name = path.split("/files/", 1)[1]
            return (
                httpx.Response(200, json=self.file_json(name))
                if name in self.files
                else problem(404, "not-found")
            )
        if "/files/" in path and request.method == "DELETE":
            name = path.split("/files/", 1)[1]
            if name not in self.files:
                return problem(404, "not-found")
            del self.files[name]
            return httpx.Response(204)
        if path.endswith("/usage"):
            return httpx.Response(
                200,
                json={
                    "volume": V,
                    "prefix": "",
                    "files": 2,
                    "bytes": 9,
                    "volume_bytes": 9,
                    "retained_bytes": 0,
                    "quota_bytes": None,
                },
            )
        return httpx.Response(200, json={"ok": True, "path": path})

    def handler(self, request):
        return self.storage(request) if request.url.host == "store.test" else self.api(request)


def make(service=None, **options):
    service = service or FakeService()
    api = DataClient(
        "http://data.test",
        token="t",
        transport=httpx.MockTransport(service.handler),
        storage_transport=httpx.MockTransport(service.handler),
        sleep=lambda _s: None,
        **options,
    )
    return api, service


# ------------------------------------------------------------------ uploads
def test_a_small_upload_is_initiate_put_complete_with_a_checksum_and_a_guessed_type():
    api, fake = make()
    info = api.files.upload(V, "docs/readme.txt", b"hello")
    assert [c[1].rsplit("/", 1)[-1] for c in fake.calls] == [
        "files:initiate-upload",
        "files:complete-upload",
    ]
    start = fake.bodies[0]
    assert start == {
        "path": "docs/readme.txt",
        "size": 5,
        "content_type": "text/plain",
        "sha256": hashlib.sha256(b"hello").hexdigest(),
    }
    assert fake.headers[0]["content-type"] == "text/plain"  # the headers the API asked for
    assert info.version == 1 and info.size == 5 and info.mtime > 0
    again = api.files.upload(
        V, "docs/readme.txt", b"hello 2", content_type="text/x-custom", checksum=False
    )
    assert (
        again.version == 2
        and "sha256" not in fake.bodies[2]
        and fake.bodies[2]["content_type"] == "text/x-custom"
    )


def test_a_large_upload_sends_parts_in_parallel_and_completes_with_their_etags():
    api, fake = make()
    data = bytes(range(256)) * 20  # 5120 bytes: five parts
    api.files.upload(V, "big.bin", data, parallelism=4)
    assert fake.files["big.bin"]["data"] == data and fake.peak > 1
    parts = fake.bodies[-1]["parts"]
    assert [p["part_number"] for p in parts] == [1, 2, 3, 4, 5]
    api2, fake2 = make()
    api2.files.upload(V, "big.bin", data, parallelism=1)
    assert fake2.peak == 1 and fake2.files["big.bin"]["data"] == data


def test_conditions_become_if_match_and_if_none_match_headers():
    api, fake = make()
    first = api.files.upload(V, "lock", b"a", if_none_match=True)
    with pytest.raises(PreconditionFailedError):
        api.files.upload(V, "lock", b"b", if_none_match=True)
    second = api.files.upload(V, "lock", b"c", if_match=first)
    assert second.version == 2
    api.files.upload(V, "lock", b"d", if_match=2)
    with pytest.raises(PreconditionFailedError):
        api.files.upload(V, "lock", b"e", if_match='"1"')
    seen = []
    api.http.event_hooks["request"] = [lambda r: seen.append(r.headers.get("if-match"))]
    api.files.upload(V, "lock", b"f", if_match=3)
    assert seen[0] == '"3"'


def test_a_quota_refusal_is_a_quota_exceeded_error_with_the_numbers():
    api, _ = make()
    with pytest.raises(QuotaExceededError) as over:
        api.files.upload(V, "over/big.bin", b"x" * 10)
    assert (over.value.used, over.value.limit) == (5, 10)


def test_uploads_accept_paths_streams_and_iterators_of_bytes(tmp_path):
    api, fake = make()
    (tmp_path / "f.bin").write_bytes(b"0123456789" * 300)
    api.files.upload(V, "from-path", tmp_path / "f.bin")
    api.files.upload(V, "from-str-path", str(tmp_path / "f.bin"))
    with (tmp_path / "f.bin").open("rb") as stream:
        api.files.upload(V, "from-stream", stream)

    class OneWay(io.RawIOBase):  # not seekable: spooled first
        def __init__(self, data):
            self.buffer = io.BytesIO(data)

        def readable(self):
            return True

        def readinto(self, target):
            return self.buffer.readinto(target)

    api.files.upload(V, "from-pipe", io.BufferedReader(OneWay(b"abc" * 1000)))
    api.files.upload(V, "from-iter", (bytes([i]) * 700 for i in range(5)))
    api.files.upload(V, "from-bytearray", bytearray(b"zz"))
    assert (
        fake.files["from-path"]["data"]
        == fake.files["from-str-path"]["data"]
        == b"0123456789" * 300
    )
    assert fake.files["from-stream"]["data"] == b"0123456789" * 300
    assert fake.files["from-pipe"]["data"] == b"abc" * 1000
    assert fake.files["from-iter"]["data"] == b"".join(bytes([i]) * 700 for i in range(5))
    assert fake.files["from-bytearray"]["data"] == b"zz"


def test_storage_hiccups_are_retried_but_an_expired_url_is_explained():
    api, fake = make()
    fake.flaky_puts = 2
    assert api.files.upload(V, "flaky", b"ok").version == 1
    fake.expired = True
    with pytest.raises(ForbiddenError) as expired:
        api.files.put_to_url("http://store.test/obj/x", b"x")
    assert (
        "expired" in expired.value.detail
        and expired.value.code == "storage-signature-does-not-match"
    )


# ------------------------------------------------------------------ reads, listing, batches
def test_reading_downloads_directly_from_the_presigned_url(tmp_path):
    api, fake = make()
    api.files.upload(V, "dir/a.bin", b"payload" * 100)
    assert api.files.read(V, "dir/a.bin") == b"payload" * 100
    target = api.files.download(V, "dir/a.bin", tmp_path / "out.bin")
    assert target.read_bytes() == b"payload" * 100 and not list(tmp_path.glob("*.part"))
    assert b"".join(api.files.iter_bytes(V, "dir/a.bin", chunk_size=64)) == b"payload" * 100
    with pytest.raises(NotFoundError):
        api.files.read(V, "dir/missing")
    with pytest.raises(NotFoundError):
        api.files.download(V, "dir/missing", tmp_path / "never.bin")
    assert not (tmp_path / "never.bin").exists() and not list(tmp_path.glob("*.part"))
    assert api.files.exists(V, "dir/a.bin") and not api.files.exists(V, "dir/missing")
    assert api.files.delete(V, "dir/a.bin") and not api.files.delete(
        V, "dir/a.bin", missing_ok=True
    )
    with pytest.raises(NotFoundError):
        api.files.delete(V, "dir/a.bin")


def test_paths_are_percent_encoded_in_urls():
    api, fake = make()
    api.files.upload(V, "a b/c#d.txt", b"x")
    api.files.info(V, "a b/c#d.txt")
    seen = []
    api.http.event_hooks["request"] = [lambda r: seen.append(r.url.raw_path.decode())]
    api.files.info(V, "a b/c#d.txt")
    assert seen == ["/api/v1/volumes/main.ws.files/files/a%20b/c%23d.txt"]


def test_listings_follow_pages_and_fold_folders():
    api, fake = make()
    for name in ("p/1", "p/2", "p/3", "p/4", "p/5", "p/sub/6", "q/7"):
        api.files.upload(V, name, name.encode())
    assert [f.path for f in api.files.list(V, "p/")] == [
        "p/1",
        "p/2",
        "p/3",
        "p/4",
        "p/5",
        "p/sub/6",
    ]
    assert (
        sum(1 for c in fake.calls if c[0] == "GET" and c[1].endswith("/files")) == 3
    )  # pages of 2
    level = api.files.list_dir(V, "p/")
    assert len(level.files) == 5 and level.directories == ["p/sub/"]
    assert api.files.usage(V).files == 2


def test_signing_is_batched_in_hundreds_and_item_errors_do_not_fail_the_batch():
    api, fake = make()
    names = [f"s/{i:03d}" for i in range(5)]
    for name in names:
        api.files.upload(V, name, name.encode())
    fake.calls.clear()
    items = [{"path": n, "action": "get"} for n in names] * 90 + [
        {"path": "nope", "action": "get"},
        {"path": "../x", "action": "get"},
    ]
    results = api.files.sign(V, items)
    assert [c[1].rsplit(":", 1)[-1] for c in fake.calls] == ["sign", "sign", "sign"]
    assert [len(b["items"]) for b in fake.bodies if "items" in b] == [200, 200, 52]
    assert len(results) == len(items) and [r["path"] for r in results] == [i["path"] for i in items]
    assert results[-2]["error"]["status"] == 404 and results[-1]["error"]["type"] == "invalid-path"


def test_put_many_and_get_many_map_each_outcome_to_its_path():
    api, fake = make()
    written = api.files.put_many(
        V, {f"m/{i}": f"v{i}".encode() for i in range(10)} | {"m/../bad": b"x"}
    )
    assert written["m/3"].version == 1 and written["m/../bad"].code == "invalid-path"
    got = api.files.get_many(V, ["m/0", "m/9", "m/none"])
    assert got["m/0"] == b"v0" and got["m/9"] == b"v9" and isinstance(got["m/none"], NotFoundError)
    assert fake.files["m/5"]["data"] == b"v5"


# ------------------------------------------------------------------ asyncio
def test_the_async_files_client_matches_the_sync_one():
    fake = FakeService()
    data = bytes(range(256)) * 12  # three parts

    async def run():
        async with AsyncDataClient(
            "http://data.test",
            token="t",
            transport=httpx.MockTransport(fake.handler),
            storage_transport=httpx.MockTransport(fake.handler),
        ) as api:
            await asyncio.gather(*(api.files.upload(V, f"z/{i}", b"x" * i) for i in range(1, 5)))
            info = await api.files.upload(V, "z/big", data, parallelism=3)
            assert info.size == len(data) and await api.files.read(V, "z/big") == data
            assert [f.path async for f in api.files.list(V, "z/")] == [
                "z/1",
                "z/2",
                "z/3",
                "z/4",
                "z/big",
            ]
            many = await api.files.get_many(V, ["z/1", "z/zz"])
            assert many["z/1"] == b"x" and isinstance(many["z/zz"], NotFoundError)
            put = await api.files.put_many(V, {"y/1": b"1", "y/../2": b"2"})
            assert put["y/1"].version == 1 and put["y/../2"].code == "invalid-path"
            with pytest.raises(PreconditionFailedError):
                await api.files.upload(V, "z/1", b"again", if_none_match=True)
            assert (await api.files.usage(V)).files == 2
            assert await api.files.exists(V, "z/1") and not await api.files.exists(V, "nope")

    asyncio.run(run())


def test_parts_cover_the_file_exactly_as_the_api_planned_them():
    api, fake = make()
    data = hashlib.sha256(b"seed").digest() * 100  # 3200 bytes: parts of 1024, 1024, 1024, 128
    api.files.upload(V, "cover", data)
    ordered = [fake.store[f"/obj/u0/{n}"] for n in range(1, 5)]
    assert [len(p) for p in ordered] == [1024, 1024, 1024, 128] and b"".join(ordered) == data
    assert re.fullmatch(r"[0-9a-f]{64}", fake.bodies[0]["sha256"])
