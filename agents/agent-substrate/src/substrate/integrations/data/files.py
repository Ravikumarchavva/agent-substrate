"""``DataFileStore``: the ``FileStore`` contract over substrate-data's Files domain (ADR 0004 and 0005).

The agent host holds no storage credentials and no database rows about files. Bytes go straight between this process
(or the browser, or the sandbox) and object storage on URLs the data service signs; the data service owns paths,
versions, quotas, ownership and audit. See ``data/substrate-data/docs/FILES.md`` and the client's ``FILESTORE_KEY_MAP.md``.

Two objects, so connections are pooled while identity stays per run:

* :class:`DataFilesConnection` is shared by the whole process: one pooled ``AsyncDataClient``, the volume resolver and the
  token provider (the seam the platform fills; ``substrate_cloud.data_access.DataTokenMinter`` mints the tokens).
* :class:`DataFileStore` is the ``FileStore`` for one ``RunScope``. It is cheap to make (one per run or per request) and
  carries that run's delegated token, so the data service, not this code, decides what the user may touch.
"""

from __future__ import annotations

import contextlib
import contextvars
import inspect
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from substrate_data_client import (
    AsyncDataClient,
    DataError,
    FileInfo,
    FileVersion,
    NotFound,
    Usage,
)
from substrate_data_client.errors import Forbidden, InsufficientScope

from substrate.integrations.data._errors import raise_for, raise_read
from substrate.integrations.data._keys import (
    VolumeResolver,
    split_key,
    split_prefix,
    volume_resolver,
)
from substrate.stores.files import WorkspacePathError
from substrate.types.run import RunScope

TokenProvider = Callable[[RunScope], "str | Awaitable[str]"]
"""Mints the bearer token a run's calls carry: ``files:write`` for this scope's user and tenant, as a delegated (``act``) token."""

DEFAULT_CONTENT_TYPE = "application/octet-stream"
_ACTING: contextvars.ContextVar[RunScope | None] = contextvars.ContextVar(
    "substrate_data_acting", default=None
)


@dataclass(frozen=True)
class UploadTicket:
    """A started direct upload: send the bytes to ``url`` (or each part's url) yourself, then ``complete_upload``."""

    key: str
    upload_id: str
    method: str  # "PUT" or "MULTIPART"
    url: str | None
    headers: dict[str, str]
    parts: list[dict[str, Any]]
    part_size: int | None
    expires_at: str | None


class DataFilesConnection:
    """The process-wide, pooled connection to substrate-data's Files API."""

    def __init__(
        self,
        url: str,
        *,
        tokens: TokenProvider,
        volumes: Mapping[str, str] | VolumeResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        storage_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120,
    ) -> None:
        self._tokens = tokens
        self.volume_of = volume_resolver(volumes)
        self.client = AsyncDataClient(
            url,
            token=self._current_token,
            timeout=timeout,
            transport=transport,
            storage_transport=storage_transport,
        )

    async def _current_token(self) -> str:
        scope = _ACTING.get()
        if scope is None:
            raise RuntimeError(
                "a substrate-data call was made outside any DataFileStore operation"
            )
        token = self._tokens(scope)
        return await token if inspect.isawaitable(token) else token

    @contextlib.contextmanager
    def acting(self, scope: RunScope) -> Iterator[None]:
        """Calls made inside carry *scope*'s token (a contextvar, so concurrent runs share the pool but never a token)."""
        reset = _ACTING.set(scope)
        try:
            yield
        finally:
            _ACTING.reset(reset)

    def store(self, scope: RunScope) -> DataFileStore:
        return DataFileStore(self, scope)

    async def aclose(self) -> None:
        await self.client.aclose()


class DataFileStore:
    """A ``FileStore`` over substrate-data for one run's identity."""

    def __init__(self, connection: DataFilesConnection, scope: RunScope) -> None:
        self._connection = connection
        self.scope = scope

    # ------------------------------------------------------------------ plumbing
    @property
    def _files(self):
        return self._connection.client.files

    def _acting(self):
        return self._connection.acting(self.scope)

    def _volume(self, tenant_id: str) -> str:
        return self._connection.volume_of(tenant_id)

    def _target(self, key: str) -> tuple[str, str, str]:
        tenant, path = split_key(key)
        if not path:
            raise WorkspacePathError(f"invalid key {key!r}: no path")
        return tenant, self._volume(tenant), path

    # ------------------------------------------------------------------ FileStore
    async def upload(
        self, key: str, data: bytes, *, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> None:
        tenant, volume, path = self._target(key)
        try:
            with self._acting():
                await self._files.upload(volume, path, data, content_type=content_type)
        except DataError as exc:
            raise_for(exc, key, tenant_id=tenant)

    async def download(self, key: str) -> bytes:
        _, volume, path = self._target(key)
        try:
            with self._acting():
                return await self._files.read(volume, path)
        except DataError as exc:
            raise_read(exc, key)

    async def exists(self, key: str) -> bool:
        _, volume, path = self._target(key)
        try:
            with self._acting():
                await self._files.info(volume, path)
        except (NotFound, Forbidden) as exc:
            if isinstance(exc, InsufficientScope):
                raise_for(exc, key)
            return False
        except DataError as exc:
            raise_for(exc, key)
        return True

    async def delete(self, key: str) -> None:
        tenant, volume, path = self._target(key)
        try:
            with self._acting():
                await self._files.delete(volume, path, missing_ok=True)
        except DataError as exc:
            raise_for(exc, key, tenant_id=tenant)

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        tenant, rest = split_prefix(prefix)
        root = f"tenants/{tenant}/"
        out: list[tuple[str, int, float]] = []
        try:
            with self._acting():
                async for info in self._files.list(self._volume(tenant), rest):
                    out.append((root + info.path, info.size, info.mtime))
        except (NotFound, Forbidden) as exc:
            if isinstance(exc, InsufficientScope):
                raise_for(exc, prefix)
            return []
        except DataError as exc:
            raise_for(exc, prefix)
        return out

    async def delete_prefix(self, prefix: str) -> int:
        tenant, rest = split_prefix(prefix)
        if not rest:
            raise WorkspacePathError(
                "refusing to delete a tenant's whole volume; name a prefix inside it"
            )
        try:
            with self._acting():
                done = await self._files.delete_prefix(self._volume(tenant), rest)
        except (NotFound, Forbidden) as exc:
            if isinstance(exc, InsufficientScope):
                raise_for(exc, prefix, tenant_id=tenant)
            return 0
        except DataError as exc:
            raise_for(exc, prefix, tenant_id=tenant)
        return int(done.get("deleted", 0))

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        source_tenant, source = split_prefix(source_prefix)
        dest_tenant, dest = split_prefix(dest_prefix)
        if source_tenant != dest_tenant:
            raise WorkspacePathError("a prefix can only be copied inside one tenant")
        if not source or not dest:
            raise WorkspacePathError(
                "copy_prefix needs prefixes inside the tenant's volume"
            )
        try:
            with self._acting():
                done = await self._files.copy_prefix(
                    self._volume(source_tenant), source, dest
                )
        except DataError as exc:
            if isinstance(exc, NotFound):
                return 0
            raise_for(exc, source_prefix, tenant_id=source_tenant)
        return int(done.get("copied", 0))

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        _, volume, path = self._target(key)
        try:
            with self._acting():
                signed = await self._files.download_url(
                    volume, path, expires_in=expires_in
                )
        except DataError as exc:
            raise_read(exc, key)
        return str(signed["url"])

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        """Exact (the catalog sums it), so ``force`` has no cache to bypass. Current, undeleted files only."""
        try:
            with self._acting():
                usage = await self._files.usage(self._volume(tenant_id))
        except (NotFound, Forbidden) as exc:
            if isinstance(exc, InsufficientScope):
                raise_for(exc, f"tenants/{tenant_id}/", tenant_id=tenant_id)
            return 0
        except DataError as exc:
            raise_for(exc, f"tenants/{tenant_id}/", tenant_id=tenant_id)
        return usage.volume_bytes

    # ------------------------------------------------------------------ beyond the protocol
    async def info(self, key: str) -> FileInfo:
        """Size, type, version and hash of the current version; ``ObjectNotFoundError`` if absent or not the caller's."""
        _, volume, path = self._target(key)
        try:
            with self._acting():
                return await self._files.info(volume, path)
        except DataError as exc:
            raise_read(exc, key)

    async def usage(self, tenant_id: str, prefix: str = "") -> Usage:
        """Files and bytes under *prefix* (a path inside the tenant's volume, such as ``users/<id>/``), with the volume's total and quota."""
        try:
            with self._acting():
                return await self._files.usage(self._volume(tenant_id), prefix)
        except DataError as exc:
            raise_for(exc, f"tenants/{tenant_id}/{prefix}", tenant_id=tenant_id)

    async def set_quota(self, tenant_id: str, max_bytes: int | None) -> None:
        """Set (or with ``None`` remove) the tenant volume's byte quota. Closed to delegated tokens: the platform's owner token only."""
        try:
            with self._acting():
                await self._files.set_quota(self._volume(tenant_id), max_bytes)
        except DataError as exc:
            raise_for(exc, f"tenants/{tenant_id}/", tenant_id=tenant_id)

    async def versions(self, key: str) -> list[FileVersion]:
        _, volume, path = self._target(key)
        try:
            with self._acting():
                return [v async for v in self._files.versions(volume, path)]
        except DataError as exc:
            raise_read(exc, key)

    async def restore(self, key: str, version: int | None = None) -> FileInfo:
        """Undelete (``version`` omitted) or make an older *version* the current one."""
        tenant, volume, path = self._target(key)
        try:
            with self._acting():
                return await self._files.restore(volume, path, version)
        except DataError as exc:
            raise_for(exc, key, tenant_id=tenant)

    async def read_version(self, key: str, version: int) -> bytes:
        _, volume, path = self._target(key)
        try:
            with self._acting():
                return await self._files.read(volume, path, version=version)
        except DataError as exc:
            raise_read(exc, key)

    async def upload_many(
        self, files: Mapping[str, bytes], *, content_type: str | None = None
    ) -> dict[str, BaseException | None]:
        """Many small files: one signing call per 200, then parallel direct transfers. Returns, per key, ``None`` or why it failed.

        A partial failure is the caller's to judge (nothing is rolled back); a key that is not a valid key fails alone."""
        results: dict[str, BaseException | None] = {}
        by_tenant: dict[str, dict[str, bytes]] = {}
        origin: dict[tuple[str, str], str] = {}
        for key, data in files.items():
            try:
                tenant, _volume, path = self._target(key)
            except WorkspacePathError as exc:
                results[key] = exc
                continue
            by_tenant.setdefault(tenant, {})[path] = data
            origin[(tenant, path)] = key
        for tenant, group in by_tenant.items():
            try:
                with self._acting():
                    done = await self._files.put_many(
                        self._volume(tenant), group, content_type=content_type
                    )
            except DataError as exc:
                for path in group:
                    results[origin[(tenant, path)]] = _typed(
                        exc, origin[(tenant, path)], tenant
                    )
                continue
            for path, outcome in done.items():
                key = origin[(tenant, path)]
                results[key] = (
                    _typed(outcome, key, tenant)
                    if isinstance(outcome, DataError)
                    else None
                )
        return results

    async def download_many(self, keys: list[str]) -> dict[str, bytes | BaseException]:
        """Many small files by batch-signed direct reads. A missing or foreign key is an ``ObjectNotFoundError`` for that key alone."""
        results: dict[str, bytes | BaseException] = {}
        by_tenant: dict[str, dict[str, str]] = {}
        for key in keys:
            try:
                tenant, _volume, path = self._target(key)
            except WorkspacePathError as exc:
                results[key] = exc
                continue
            by_tenant.setdefault(tenant, {})[path] = key
        for tenant, paths in by_tenant.items():
            try:
                with self._acting():
                    got = await self._files.get_many(self._volume(tenant), list(paths))
            except DataError as exc:
                for key in paths.values():
                    results[key] = _typed(exc, key, tenant, read=True)
                continue
            for path, outcome in got.items():
                key = paths[path]
                results[key] = (
                    _typed(outcome, key, tenant, read=True)
                    if isinstance(outcome, DataError)
                    else outcome
                )
        return results

    # -------------------------------------------------- direct upload by a browser or sandbox
    async def initiate_upload(
        self,
        key: str,
        size: int,
        *,
        content_type: str | None = None,
        sha256: str | None = None,
        if_none_match: bool = False,
    ) -> UploadTicket:
        """Start an upload whose bytes this process never sees: hand ``ticket.url`` (or its ``parts``) to the sender, then ``complete_upload``."""
        tenant, volume, path = self._target(key)
        try:
            with self._acting():
                started = await self._files.initiate_upload(
                    volume,
                    path,
                    size,
                    content_type=content_type,
                    sha256=sha256,
                    if_none_match=if_none_match,
                )
        except DataError as exc:
            raise_for(exc, key, tenant_id=tenant)
        return UploadTicket(
            key=key,
            upload_id=started["upload_id"],
            method=started["method"],
            url=started.get("url"),
            headers=dict(started.get("headers") or {}),
            parts=list(started.get("parts") or []),
            part_size=started.get("part_size"),
            expires_at=started.get("expires_at"),
        )

    async def complete_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]] | None = None,
        *,
        if_none_match: bool = False,
    ) -> FileInfo:
        tenant, volume, _path = self._target(key)
        try:
            with self._acting():
                return await self._files.complete_upload(
                    volume, upload_id, parts, if_none_match=if_none_match
                )
        except DataError as exc:
            raise_for(exc, key, tenant_id=tenant)

    async def download_url(
        self, key: str, *, expires_in: int = 3600, version: int | None = None
    ) -> str:
        _, volume, path = self._target(key)
        try:
            with self._acting():
                signed = await self._files.download_url(
                    volume, path, version=version, expires_in=expires_in
                )
        except DataError as exc:
            raise_read(exc, key)
        return str(signed["url"])

    async def list_dir(
        self, prefix: str
    ) -> tuple[list[tuple[str, int, float]], list[str]]:
        """One level under *prefix*: ``(files, sub-folders)``, keys full. Sub-folders end in ``/``."""
        tenant, rest = split_prefix(prefix)
        root = f"tenants/{tenant}/"
        try:
            with self._acting():
                listing = await self._files.list_dir(self._volume(tenant), rest)
        except (NotFound, Forbidden) as exc:
            if isinstance(exc, InsufficientScope):
                raise_for(exc, prefix)
            return [], []
        except DataError as exc:
            raise_for(exc, prefix)
        return (
            [(root + f.path, f.size, f.mtime) for f in listing.files],
            [root + d for d in listing.directories],
        )


class KeyScopedFileStore:
    """A ``FileStore`` for consumers built once per process (the document library, the artifact store, the sandbox's blob store).

    They hold a store for their whole life, so they cannot be handed a store per run. Each call here acts as the owner the *key*
    names: ``tenants/{t}/users/{u}/...`` is done as user ``u`` of tenant ``t``; a key outside ``users/`` (a knowledge base, a shared
    folder) is done as ``service_user``, who owns nothing and reaches only what the tenant's grants allow. The data service still
    enforces the ownership rule on every call. Code that knows its run builds a :class:`DataFileStore` for that run instead.
    """

    def __init__(
        self, connection: DataFilesConnection, *, service_user: str = "platform"
    ) -> None:
        self._connection = connection
        self._service_user = service_user

    def _for(self, key: str, *, prefix: bool = False) -> DataFileStore:
        tenant, path = split_prefix(key) if prefix else split_key(key)
        parts = path.split("/")
        user = (
            parts[1]
            if len(parts) > 2 and parts[0] == "users" and parts[1]
            else self._service_user
        )
        if prefix and len(parts) == 2 and parts[0] == "users" and parts[1]:
            user = parts[1]
        return self._connection.store(RunScope(tenant_id=tenant, user_id=user))

    async def upload(
        self, key: str, data: bytes, *, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> None:
        await self._for(key).upload(key, data, content_type=content_type)

    async def download(self, key: str) -> bytes:
        return await self._for(key).download(key)

    async def exists(self, key: str) -> bool:
        return await self._for(key).exists(key)

    async def delete(self, key: str) -> None:
        await self._for(key).delete(key)

    async def list_prefix(self, prefix: str) -> list[tuple[str, int, float]]:
        return await self._for(prefix, prefix=True).list_prefix(prefix)

    async def delete_prefix(self, prefix: str) -> int:
        return await self._for(prefix, prefix=True).delete_prefix(prefix)

    async def copy_prefix(self, source_prefix: str, dest_prefix: str) -> int:
        return await self._for(source_prefix, prefix=True).copy_prefix(
            source_prefix, dest_prefix
        )

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        return await self._for(key).presign_url(key, expires_in=expires_in)

    async def usage_bytes(self, tenant_id: str, *, force: bool = False) -> int:
        return await self._connection.store(
            RunScope(tenant_id=tenant_id, user_id=self._service_user)
        ).usage_bytes(tenant_id, force=force)

    async def aclose(self) -> None:
        await self._connection.aclose()


def _typed(
    exc: DataError, key: str, tenant: str, *, read: bool = False
) -> BaseException:
    """The engine error for *exc* as a value (batch results carry errors instead of raising them)."""
    try:
        if read:
            raise_read(exc, key)
        raise_for(exc, key, tenant_id=tenant)
    except Exception as typed:  # noqa: BLE001 - the typed error is the result
        return typed


__all__ = [
    "DataFileStore",
    "DataFilesConnection",
    "KeyScopedFileStore",
    "TokenProvider",
    "UploadTicket",
]
