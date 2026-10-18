"""`client.files`: governed files in volumes, with bytes going straight to and from object storage.

    files = client.files
    files.upload("main.ws.files", "reports/q3.csv", "q3.csv")            # path, bytes, file object
    data = files.read("main.ws.files", "reports/q3.csv")
    for f in files.list("main.ws.files", "reports/"): print(f.path, f.size)
    files.upload("main.ws.files", "lock", b"me", if_none_match=True)      # create only
    info = files.info("main.ws.files", "doc.txt")
    files.upload("main.ws.files", "doc.txt", b"v2", if_match=info)        # compare-and-swap

The API only returns presigned URLs; this module sends and fetches the bytes itself (large files in
parallel parts) and then tells the API to commit. The low-level calls (`initiate_upload`,
`complete_upload`, `sign`, `download_url`) are public for adapters that move bytes some other way.
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from substrate_data_client._http import AsyncCore, SyncCore
from substrate_data_client._sources import Source, UploadSource, open_source
from substrate_data_client.errors import DataError, NotFound, error_for
from substrate_data_client.models import FileInfo, FileVersion, Listing, Usage

MAX_SIGN_ITEMS = 200
DEFAULT_CONTENT_TYPE = "application/octet-stream"
DOWNLOAD_CHUNK = 1024 * 1024


# ------------------------------------------------------------------ shared helpers
def _quote(path: str) -> str:
    return quote(path, safe="/")


def _version_of(value: int | str | FileInfo) -> str:
    if isinstance(value, FileInfo):
        return value.etag
    return value if isinstance(value, str) else f'"{value}"'


def _conditions(if_match: int | str | FileInfo | None, if_none_match: bool) -> dict[str, str]:
    headers = {}
    if if_match is not None:
        headers["If-Match"] = _version_of(if_match)
    if if_none_match:
        headers["If-None-Match"] = "*"
    return headers


def _content_type(path: str, given: str | None) -> str:
    return given or mimetypes.guess_type(path)[0] or DEFAULT_CONTENT_TYPE


def _storage_failure(response: httpx.Response) -> DataError:
    """Object storage answers in XML: keep its code and message as a problem."""
    text = response.text[:2000]
    code = re.search(r"<Code>([^<]+)</Code>", text)
    message = re.search(r"<Message>([^<]+)</Message>", text)
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", code.group(1)).lower() if code else "storage-error"
    detail = message.group(1) if message else f"Object storage answered {response.status_code}"
    status = response.status_code
    if status == 403:
        detail += " (the presigned URL may have expired; request a new one)"
    return error_for(status, {"type": f"storage-{slug}", "detail": detail})


def _part_etag(response: httpx.Response) -> str:
    return response.headers.get("etag", "").strip('"')


def _sign_chunks(items: list[dict[str, Any]]) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(items), MAX_SIGN_ITEMS):
        yield items[start : start + MAX_SIGN_ITEMS]


def _listing_params(
    prefix: str, delimiter: str | None, page_token: str | None, page_size: int | None
):
    params: dict[str, Any] = {"prefix": prefix}
    if delimiter:
        params["delimiter"] = delimiter
    if page_token:
        params["page_token"] = page_token
    if page_size:
        params["page_size"] = page_size
    return params


class _Base:
    @staticmethod
    def _url(volume: str, suffix: str) -> str:
        return f"/volumes/{volume}/{suffix}"

    @staticmethod
    def _file_url(volume: str, path: str, action: str = "") -> str:
        return f"/volumes/{volume}/files/{_quote(path)}{action}"


# ------------------------------------------------------------------ synchronous
class Files(_Base):
    def __init__(self, core: SyncCore, storage: SyncCore):
        self._core, self._storage = core, storage

    # -------------------------------------------------------------- low level: the API
    def initiate_upload(
        self,
        volume: str,
        path: str,
        size: int,
        *,
        content_type: str | None = None,
        sha256: str | None = None,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
    ) -> dict[str, Any]:
        """Start an upload. Returns `method` (`PUT` or `MULTIPART`), URLs, `upload_id`, `expires_at`."""
        body = {"path": path, "size": size, "content_type": content_type, "sha256": sha256}
        return self._core.call(
            "POST",
            self._url(volume, "files:initiate-upload"),
            json={k: v for k, v in body.items() if v is not None},
            headers=_conditions(if_match, if_none_match),
        )

    def complete_upload(
        self,
        volume: str,
        upload_id: str,
        parts: list[dict[str, Any]] | None = None,
        *,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
    ) -> FileInfo:
        """Commit a finished upload as the file's new version. Safe to retry."""
        return FileInfo.from_api(
            self._core.call(
                "POST",
                self._url(volume, "files:complete-upload"),
                json={"upload_id": upload_id, "parts": parts or []},
                headers=_conditions(if_match, if_none_match),
                idempotent=True,
            )
        )

    def sign(self, volume: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Presigned URLs for many files (`{"path", "action": "get"|"put", "size"?}`), 200 per call.
        An item that cannot be served carries an `error` instead of a `url`."""
        results: list[dict[str, Any]] = []
        for chunk in _sign_chunks(items):
            results += self._core.call(
                "POST", self._url(volume, "files:sign"), json={"items": chunk}, idempotent=True
            )["items"]
        return results

    def list_page(
        self,
        volume: str,
        prefix: str = "",
        *,
        delimiter: str | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return self._core.call(
            "GET",
            self._url(volume, "files"),
            params=_listing_params(prefix, delimiter, page_token, page_size),
        )

    def list(
        self, volume: str, prefix: str = "", *, page_size: int | None = None
    ) -> Iterator[FileInfo]:
        """Every file whose path starts with `prefix`, in path order (pages are fetched lazily)."""
        token = None
        while True:
            page = self.list_page(volume, prefix, page_token=token, page_size=page_size)
            for item in page["files"]:
                yield FileInfo.from_api(item)
            token = page["next_page_token"]
            if not token:
                return

    def list_dir(self, volume: str, prefix: str = "") -> Listing:
        """One folder level: the files directly under `prefix` and its sub-folders (`name/`)."""
        files: list[FileInfo] = []
        directories: list[str] = []
        token = None
        while True:
            page = self.list_page(volume, prefix, delimiter="/", page_token=token)
            files += [FileInfo.from_api(i) for i in page["files"]]
            directories += page["directories"]
            token = page["next_page_token"]
            if not token:
                return Listing(files, directories)

    def info(self, volume: str, path: str) -> FileInfo:
        return FileInfo.from_api(self._core.call("GET", self._file_url(volume, path)))

    def exists(self, volume: str, path: str) -> bool:
        try:
            self.info(volume, path)
        except NotFound:
            return False
        return True

    def download_url(
        self,
        volume: str,
        path: str,
        *,
        version: int | None = None,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        params = {k: v for k, v in {"version": version, "expires_in": expires_in}.items() if v}
        return self._core.call("GET", self._file_url(volume, path, ":download-url"), params=params)

    def delete(
        self,
        volume: str,
        path: str,
        *,
        if_match: int | str | FileInfo | None = None,
        missing_ok: bool = False,
    ) -> bool:
        """Soft-delete a file (its versions stay until purged). False if it was already gone and
        `missing_ok`."""
        try:
            self._core.call(
                "DELETE", self._file_url(volume, path), headers=_conditions(if_match, False)
            )
        except NotFound:
            if missing_ok:
                return False
            raise
        return True

    def delete_prefix(self, volume: str, prefix: str) -> dict[str, int]:
        return self._core.call(
            "POST", self._url(volume, "files:delete-prefix"), json={"prefix": prefix}
        )

    def copy_prefix(
        self, volume: str, source_prefix: str, dest_prefix: str, *, overwrite: bool = True
    ) -> dict[str, int]:
        """Server-side copy of a folder inside the volume; returns `copied`, `skipped`, `bytes`."""
        return self._core.call(
            "POST",
            self._url(volume, "files:copy-prefix"),
            json={
                "source_prefix": source_prefix,
                "dest_prefix": dest_prefix,
                "overwrite": overwrite,
            },
        )

    def versions(self, volume: str, path: str) -> Iterator[FileVersion]:
        """Newest first."""
        token = None
        while True:
            page = self._core.call(
                "GET",
                self._file_url(volume, path, "/versions"),
                params={"page_token": token} if token else None,
            )
            for item in page["versions"]:
                yield FileVersion.from_api(item)
            token = page["next_page_token"]
            if not token:
                return

    def restore(
        self,
        volume: str,
        path: str,
        version: int | None = None,
        *,
        if_match: int | str | FileInfo | None = None,
    ) -> FileInfo:
        """Undelete the file, or make `version` current again (as a new version)."""
        return FileInfo.from_api(
            self._core.call(
                "POST",
                self._file_url(volume, path, ":restore"),
                json={"version": version},
                headers=_conditions(if_match, False),
            )
        )

    def usage(self, volume: str, prefix: str = "") -> Usage:
        return Usage.from_api(
            self._core.call("GET", self._url(volume, "usage"), params={"prefix": prefix})
        )

    def set_quota(self, volume: str, max_bytes: int | None) -> dict[str, Any]:
        """Set (or, with None, remove) the volume's hard byte quota. Owners and admins only."""
        return self._core.call("PUT", self._url(volume, "quotas"), json={"max_bytes": max_bytes})

    def create_session(
        self, size: int, *, content_type: str | None = None, sha256: str | None = None
    ) -> dict[str, Any]:
        """A pending upload outside any volume (expires after the server's TTL, 24 h by default)."""
        body = {"size": size, "content_type": content_type, "sha256": sha256}
        return self._core.call(
            "POST", "/upload-sessions", json={k: v for k, v in body.items() if v is not None}
        )

    def promote_session(
        self,
        session_id: str,
        volume: str,
        path: str,
        parts: list[dict[str, Any]] | None = None,
        *,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
    ) -> FileInfo:
        return FileInfo.from_api(
            self._core.call(
                "POST",
                f"/upload-sessions/{session_id}:promote",
                json={"volume": volume, "path": path, "parts": parts or []},
                headers=_conditions(if_match, if_none_match),
            )
        )

    # -------------------------------------------------------------- direct to storage
    def put_to_url(self, url: str, data: bytes, headers: dict[str, str] | None = None) -> str:
        """PUT bytes to a presigned URL (retried on 5xx); returns the ETag."""
        response = self._storage.send("PUT", url, content=data, headers=headers)
        if response.status_code >= 400:
            response.read()
            raise _storage_failure(response)
        return _part_etag(response)

    def _send_parts(
        self, source: UploadSource, parts: list[dict[str, Any]], parallelism: int
    ) -> list[dict[str, Any]]:
        def one(part: dict[str, Any]) -> dict[str, Any]:
            etag = self.put_to_url(part["url"], source.read(part["offset"], part["length"]))
            return {"part_number": part["part_number"], "etag": etag}

        with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
            return list(pool.map(one, parts))

    def upload(
        self,
        volume: str,
        path: str,
        source: Source,
        *,
        content_type: str | None = None,
        checksum: bool = True,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
        parallelism: int = 8,
    ) -> FileInfo:
        """Upload bytes, a local file path or a binary file object as the file's next version.

        Up to 64 MiB goes in one request; more is split into parts sent in parallel. With
        `checksum=True` (default) the SHA-256 is sent so the store verifies single-request uploads."""
        opened = open_source(source)
        try:
            started = self.initiate_upload(
                volume,
                path,
                opened.size,
                content_type=_content_type(path, content_type),
                sha256=opened.sha256() if checksum else None,
                if_match=if_match,
                if_none_match=if_none_match,
            )
            parts = None
            if started["method"] == "PUT":
                self.put_to_url(started["url"], opened.read(0, opened.size), started.get("headers"))
            else:
                parts = self._send_parts(opened, started["parts"], parallelism)
            return self.complete_upload(
                volume, started["upload_id"], parts, if_match=if_match, if_none_match=if_none_match
            )
        finally:
            opened.close()

    def read(self, volume: str, path: str, *, version: int | None = None) -> bytes:
        return b"".join(self.iter_bytes(volume, path, version=version))

    def iter_bytes(
        self,
        volume: str,
        path: str,
        *,
        version: int | None = None,
        chunk_size: int = DOWNLOAD_CHUNK,
    ) -> Iterator[bytes]:
        """The file's bytes in chunks, fetched directly from storage."""
        url = self.download_url(volume, path, version=version)["url"]
        response = self._storage.send("GET", url, stream=True)
        try:
            if response.status_code >= 400:
                response.read()
                raise _storage_failure(response)
            yield from response.iter_bytes(chunk_size)
        finally:
            response.close()

    def download(
        self, volume: str, path: str, destination: str | Path, *, version: int | None = None
    ) -> Path:
        """Stream a file to disk (written to a temporary name, then moved into place)."""
        target = Path(destination)
        partial = target.with_name(target.name + ".part")
        try:
            with partial.open("wb") as out:
                for chunk in self.iter_bytes(volume, path, version=version):
                    out.write(chunk)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        return target

    # -------------------------------------------------------------- batches of small files
    def put_many(
        self,
        volume: str,
        files: dict[str, bytes],
        *,
        content_type: str | None = None,
        parallelism: int = 16,
    ) -> dict[str, FileInfo | DataError]:
        """Write many small files (each up to 64 MiB): one signing call per 200, parallel transfers.
        Each path maps to its `FileInfo`, or to the error that stopped it (nothing is raised)."""
        outcome: dict[str, FileInfo | DataError] = {}
        items = [
            {
                "path": p,
                "action": "put",
                "size": len(d),
                "content_type": _content_type(p, content_type),
            }
            for p, d in files.items()
        ]
        signed = self.sign(volume, items)

        def one(entry: dict[str, Any]) -> tuple[str, FileInfo | DataError]:
            path = entry["path"]
            if "error" in entry:
                return path, error_for(entry["error"]["status"], entry["error"])
            try:
                self.put_to_url(entry["url"], files[path], entry.get("headers"))
                return path, self.complete_upload(volume, entry["upload_id"])
            except DataError as exc:
                return path, exc

        with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
            outcome.update(pool.map(one, signed))
        return outcome

    def get_many(
        self, volume: str, paths: list[str], *, parallelism: int = 16
    ) -> dict[str, bytes | DataError]:
        """Read many small files: batch URL signing, then parallel direct downloads."""
        signed = self.sign(volume, [{"path": p, "action": "get"} for p in paths])

        def one(entry: dict[str, Any]) -> tuple[str, bytes | DataError]:
            if "error" in entry:
                return entry["path"], error_for(entry["error"]["status"], entry["error"])
            response = self._storage.send("GET", entry["url"])
            if response.status_code >= 400:
                return entry["path"], _storage_failure(response)
            return entry["path"], response.content

        with ThreadPoolExecutor(max_workers=max(1, parallelism)) as pool:
            return dict(pool.map(one, signed))


# ------------------------------------------------------------------ asynchronous
class AsyncFiles(_Base):
    def __init__(self, core: AsyncCore, storage: AsyncCore):
        self._core, self._storage = core, storage

    async def initiate_upload(
        self,
        volume: str,
        path: str,
        size: int,
        *,
        content_type: str | None = None,
        sha256: str | None = None,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
    ) -> dict[str, Any]:
        body = {"path": path, "size": size, "content_type": content_type, "sha256": sha256}
        return await self._core.call(
            "POST",
            self._url(volume, "files:initiate-upload"),
            json={k: v for k, v in body.items() if v is not None},
            headers=_conditions(if_match, if_none_match),
        )

    async def complete_upload(
        self,
        volume: str,
        upload_id: str,
        parts: list[dict[str, Any]] | None = None,
        *,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
    ) -> FileInfo:
        return FileInfo.from_api(
            await self._core.call(
                "POST",
                self._url(volume, "files:complete-upload"),
                json={"upload_id": upload_id, "parts": parts or []},
                headers=_conditions(if_match, if_none_match),
                idempotent=True,
            )
        )

    async def sign(self, volume: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for chunk in _sign_chunks(items):
            body = await self._core.call(
                "POST", self._url(volume, "files:sign"), json={"items": chunk}, idempotent=True
            )
            results += body["items"]
        return results

    async def list_page(
        self,
        volume: str,
        prefix: str = "",
        *,
        delimiter: str | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        return await self._core.call(
            "GET",
            self._url(volume, "files"),
            params=_listing_params(prefix, delimiter, page_token, page_size),
        )

    async def list(
        self, volume: str, prefix: str = "", *, page_size: int | None = None
    ) -> AsyncIterator[FileInfo]:
        token = None
        while True:
            page = await self.list_page(volume, prefix, page_token=token, page_size=page_size)
            for item in page["files"]:
                yield FileInfo.from_api(item)
            token = page["next_page_token"]
            if not token:
                return

    async def list_dir(self, volume: str, prefix: str = "") -> Listing:
        files: list[FileInfo] = []
        directories: list[str] = []
        token = None
        while True:
            page = await self.list_page(volume, prefix, delimiter="/", page_token=token)
            files += [FileInfo.from_api(i) for i in page["files"]]
            directories += page["directories"]
            token = page["next_page_token"]
            if not token:
                return Listing(files, directories)

    async def info(self, volume: str, path: str) -> FileInfo:
        return FileInfo.from_api(await self._core.call("GET", self._file_url(volume, path)))

    async def exists(self, volume: str, path: str) -> bool:
        try:
            await self.info(volume, path)
        except NotFound:
            return False
        return True

    async def download_url(
        self,
        volume: str,
        path: str,
        *,
        version: int | None = None,
        expires_in: int | None = None,
    ) -> dict[str, Any]:
        params = {k: v for k, v in {"version": version, "expires_in": expires_in}.items() if v}
        return await self._core.call(
            "GET", self._file_url(volume, path, ":download-url"), params=params
        )

    async def delete(
        self,
        volume: str,
        path: str,
        *,
        if_match: int | str | FileInfo | None = None,
        missing_ok: bool = False,
    ) -> bool:
        try:
            await self._core.call(
                "DELETE", self._file_url(volume, path), headers=_conditions(if_match, False)
            )
        except NotFound:
            if missing_ok:
                return False
            raise
        return True

    async def delete_prefix(self, volume: str, prefix: str) -> dict[str, int]:
        return await self._core.call(
            "POST", self._url(volume, "files:delete-prefix"), json={"prefix": prefix}
        )

    async def copy_prefix(
        self, volume: str, source_prefix: str, dest_prefix: str, *, overwrite: bool = True
    ) -> dict[str, int]:
        return await self._core.call(
            "POST",
            self._url(volume, "files:copy-prefix"),
            json={
                "source_prefix": source_prefix,
                "dest_prefix": dest_prefix,
                "overwrite": overwrite,
            },
        )

    async def versions(self, volume: str, path: str) -> AsyncIterator[FileVersion]:
        token = None
        while True:
            page = await self._core.call(
                "GET",
                self._file_url(volume, path, "/versions"),
                params={"page_token": token} if token else None,
            )
            for item in page["versions"]:
                yield FileVersion.from_api(item)
            token = page["next_page_token"]
            if not token:
                return

    async def restore(
        self,
        volume: str,
        path: str,
        version: int | None = None,
        *,
        if_match: int | str | FileInfo | None = None,
    ) -> FileInfo:
        return FileInfo.from_api(
            await self._core.call(
                "POST",
                self._file_url(volume, path, ":restore"),
                json={"version": version},
                headers=_conditions(if_match, False),
            )
        )

    async def usage(self, volume: str, prefix: str = "") -> Usage:
        return Usage.from_api(
            await self._core.call("GET", self._url(volume, "usage"), params={"prefix": prefix})
        )

    async def set_quota(self, volume: str, max_bytes: int | None) -> dict[str, Any]:
        return await self._core.call(
            "PUT", self._url(volume, "quotas"), json={"max_bytes": max_bytes}
        )

    async def create_session(
        self, size: int, *, content_type: str | None = None, sha256: str | None = None
    ) -> dict[str, Any]:
        body = {"size": size, "content_type": content_type, "sha256": sha256}
        return await self._core.call(
            "POST", "/upload-sessions", json={k: v for k, v in body.items() if v is not None}
        )

    async def promote_session(
        self,
        session_id: str,
        volume: str,
        path: str,
        parts: list[dict[str, Any]] | None = None,
        *,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
    ) -> FileInfo:
        return FileInfo.from_api(
            await self._core.call(
                "POST",
                f"/upload-sessions/{session_id}:promote",
                json={"volume": volume, "path": path, "parts": parts or []},
                headers=_conditions(if_match, if_none_match),
            )
        )

    # -------------------------------------------------------------- direct to storage
    async def put_to_url(self, url: str, data: bytes, headers: dict[str, str] | None = None) -> str:
        response = await self._storage.send("PUT", url, content=data, headers=headers)
        if response.status_code >= 400:
            await response.aread()
            raise _storage_failure(response)
        return _part_etag(response)

    async def _send_parts(
        self, source: UploadSource, parts: list[dict[str, Any]], parallelism: int
    ) -> list[dict[str, Any]]:
        gate = asyncio.Semaphore(max(1, parallelism))

        async def one(part: dict[str, Any]) -> dict[str, Any]:
            async with gate:  # also bounds how many parts are held in memory
                data = await asyncio.to_thread(source.read, part["offset"], part["length"])
                etag = await self.put_to_url(part["url"], data)
            return {"part_number": part["part_number"], "etag": etag}

        return list(await asyncio.gather(*(one(p) for p in parts)))

    async def upload(
        self,
        volume: str,
        path: str,
        source: Source,
        *,
        content_type: str | None = None,
        checksum: bool = True,
        if_match: int | str | FileInfo | None = None,
        if_none_match: bool = False,
        parallelism: int = 8,
    ) -> FileInfo:
        opened = await asyncio.to_thread(open_source, source)
        try:
            started = await self.initiate_upload(
                volume,
                path,
                opened.size,
                content_type=_content_type(path, content_type),
                sha256=(await asyncio.to_thread(opened.sha256)) if checksum else None,
                if_match=if_match,
                if_none_match=if_none_match,
            )
            parts = None
            if started["method"] == "PUT":
                data = await asyncio.to_thread(opened.read, 0, opened.size)
                await self.put_to_url(started["url"], data, started.get("headers"))
            else:
                parts = await self._send_parts(opened, started["parts"], parallelism)
            return await self.complete_upload(
                volume, started["upload_id"], parts, if_match=if_match, if_none_match=if_none_match
            )
        finally:
            opened.close()

    async def read(self, volume: str, path: str, *, version: int | None = None) -> bytes:
        return b"".join([chunk async for chunk in self.iter_bytes(volume, path, version=version)])

    async def iter_bytes(
        self,
        volume: str,
        path: str,
        *,
        version: int | None = None,
        chunk_size: int = DOWNLOAD_CHUNK,
    ) -> AsyncIterator[bytes]:
        url = (await self.download_url(volume, path, version=version))["url"]
        response = await self._storage.send("GET", url, stream=True)
        try:
            if response.status_code >= 400:
                await response.aread()
                raise _storage_failure(response)
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk
        finally:
            await response.aclose()

    async def download(
        self, volume: str, path: str, destination: str | Path, *, version: int | None = None
    ) -> Path:
        target = Path(destination)
        partial = target.with_name(target.name + ".part")
        try:
            with partial.open("wb") as out:
                async for chunk in self.iter_bytes(volume, path, version=version):
                    await asyncio.to_thread(out.write, chunk)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        return target

    async def put_many(
        self,
        volume: str,
        files: dict[str, bytes],
        *,
        content_type: str | None = None,
        parallelism: int = 16,
    ) -> dict[str, FileInfo | DataError]:
        items = [
            {
                "path": p,
                "action": "put",
                "size": len(d),
                "content_type": _content_type(p, content_type),
            }
            for p, d in files.items()
        ]
        signed = await self.sign(volume, items)
        gate = asyncio.Semaphore(max(1, parallelism))

        async def one(entry: dict[str, Any]) -> tuple[str, FileInfo | DataError]:
            path = entry["path"]
            if "error" in entry:
                return path, error_for(entry["error"]["status"], entry["error"])
            async with gate:
                try:
                    await self.put_to_url(entry["url"], files[path], entry.get("headers"))
                    return path, await self.complete_upload(volume, entry["upload_id"])
                except DataError as exc:
                    return path, exc

        return dict(await asyncio.gather(*(one(e) for e in signed)))

    async def get_many(
        self, volume: str, paths: list[str], *, parallelism: int = 16
    ) -> dict[str, bytes | DataError]:
        signed = await self.sign(volume, [{"path": p, "action": "get"} for p in paths])
        gate = asyncio.Semaphore(max(1, parallelism))

        async def one(entry: dict[str, Any]) -> tuple[str, bytes | DataError]:
            if "error" in entry:
                return entry["path"], error_for(entry["error"]["status"], entry["error"])
            async with gate:
                response = await self._storage.send("GET", entry["url"])
            if response.status_code >= 400:
                return entry["path"], _storage_failure(response)
            return entry["path"], response.content

        return dict(await asyncio.gather(*(one(e) for e in signed)))
