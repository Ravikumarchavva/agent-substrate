"""`client.erasure` and `client.exports`: one person's (or one tenant's) data, removed or handed over.

    receipt = client.erasure.request(subject="u1")            # files + versions, uploads, libraries, vectors, memory
    bundle = client.exports.create(subject="u1")              # JSON manifest with presigned file URLs

The subject itself, a tenant administrator or a platform administrator may ask; delegated (agent) tokens never can.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loamz.data._http import AsyncCore, SyncCore


def _target(subject: str | None, tenant_id: str | None, everything: bool) -> dict[str, Any]:
    if everything == (subject is not None):
        raise ValueError("name a subject, or pass everything=True for the whole tenant")
    body: dict[str, Any] = {"scope": "tenant" if everything else "user"}
    if subject is not None:
        body["subject"] = subject
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    return body


class _PrivacyCalls:
    _path = ""

    def _do(self, method: str, path: str, post: Callable | None = None, **kwargs: Any):
        raise NotImplementedError

    def _create(self, subject: str | None, tenant_id: str | None, everything: bool):
        return self._do("POST", self._path, json=_target(subject, tenant_id, everything))


class _ErasureCalls(_PrivacyCalls):
    _path = "/erasure-requests"

    def request(
        self, subject: str | None = None, *, tenant_id: str | None = None, everything: bool = False
    ):
        """Erase a subject's data (or, with `everything=True`, the whole tenant's). Returns the receipt."""
        return self._create(subject, tenant_id, everything)


class _ExportCalls(_PrivacyCalls):
    _path = "/exports"

    def create(
        self, subject: str | None = None, *, tenant_id: str | None = None, everything: bool = False
    ):
        """Everything held about a subject (or the tenant) as one JSON manifest."""
        return self._create(subject, tenant_id, everything)


class Erasure(_ErasureCalls):
    def __init__(self, core: SyncCore):
        self._core = core

    def _do(self, method, path, post=None, **kwargs):
        return self._core.call(method, path, **kwargs)


class AsyncErasure(_ErasureCalls):
    def __init__(self, core: AsyncCore):
        self._core = core

    async def _do(self, method, path, post=None, **kwargs):
        return await self._core.call(method, path, **kwargs)


class Exports(_ExportCalls):
    def __init__(self, core: SyncCore):
        self._core = core

    def _do(self, method, path, post=None, **kwargs):
        return self._core.call(method, path, **kwargs)


class AsyncExports(_ExportCalls):
    def __init__(self, core: AsyncCore):
        self._core = core

    async def _do(self, method, path, post=None, **kwargs):
        return await self._core.call(method, path, **kwargs)
