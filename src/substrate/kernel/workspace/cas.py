"""BlobCAS — content-addressed blob storage over an ObjectStore.

Whole-file sha256 addressing (see ``kernel/storage/snapshots.py::ContentRef``
for why this is the deliberate v1 choice, and the escape hatch for adding
content-defined chunking later without a format break). Blobs are scoped per
user (``layout.py::blob_key``), not per tenant or globally — see that
function's docstring for why (GDPR erasure, GC incrementality, no
cross-tenant dedup).

No garbage collection here. Per the workspace plan's decided GC posture:
v1 reclaims space only via explicit prefix deletion (a user or conversation
being erased takes its blobs with it, since they live under the same
``tenants/{t}/users/{u}/`` prefix everything else does) — no blob-level
mark-and-sweep yet.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from substrate.kernel.storage.objects import ObjectStore
from substrate.kernel.storage.snapshots import ContentRef

from .layout import blob_key


class BlobCAS:
    """Content-addressed blob store for one user, backed by an ``ObjectStore``.

    ``local_cache_dir``, if given, is a local scratch directory this CAS
    also writes every blob into (keyed by hash) — populated on both ``put``
    and ``get``. ``materialize.py`` hardlinks from this cache instead of
    copying bytes when checking out a snapshot, which is what makes
    materialization near-instant after the first time a blob is touched,
    regardless of whether the backing ``ObjectStore`` is local disk or S3.
    Optional: without it, ``get`` just downloads every time.
    """

    def __init__(
        self,
        store: ObjectStore,
        *,
        tenant_id: str,
        user_id: str,
        local_cache_dir: Path | None = None,
    ) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._local_cache_dir = local_cache_dir

    def cache_path(self, ref: ContentRef) -> Path | None:
        """The local cache file for *ref*, or ``None`` if no cache is configured."""
        if self._local_cache_dir is None:
            return None
        return self._local_cache_dir / ref.hash[:2] / ref.hash

    async def put(self, data: bytes) -> ContentRef:
        """Store *data*, deduplicating on content hash.

        A second ``put`` of identical bytes is a cheap ``exists`` check, not
        a second upload — the whole point of content addressing.
        """
        digest = hashlib.sha256(data).hexdigest()
        ref = ContentRef(kind="blob", hash=digest, size_bytes=len(data))
        key = blob_key(self._tenant_id, self._user_id, digest)
        if not await self._store.exists(key):
            await self._store.upload(key, data)
        self._warm_cache(ref, data)
        return ref

    async def get(self, ref: ContentRef) -> bytes:
        """Fetch the bytes a ``ContentRef`` addresses.

        Only ``kind == "blob"`` is implemented — v1 never produces
        ``"chunked"`` refs (see ``ContentRef``'s own docstring); a chunked
        ref reaching here means a future CDC reader hasn't been wired up
        yet, which is a real bug, not a silent fallback.
        """
        if ref.kind != "blob":
            raise NotImplementedError(
                f"BlobCAS.get: content ref kind {ref.kind!r} not supported "
                "(only whole-file 'blob' refs exist in this codebase today)"
            )
        cache_path = self.cache_path(ref)
        if cache_path is not None and cache_path.exists():
            return cache_path.read_bytes()
        key = blob_key(self._tenant_id, self._user_id, ref.hash)
        data = await self._store.download(key)
        self._warm_cache(ref, data)
        return data

    async def has(self, ref: ContentRef) -> bool:
        cache_path = self.cache_path(ref)
        if cache_path is not None and cache_path.exists():
            return True
        key = blob_key(self._tenant_id, self._user_id, ref.hash)
        return await self._store.exists(key)

    def _warm_cache(self, ref: ContentRef, data: bytes) -> None:
        cache_path = self.cache_path(ref)
        if cache_path is None or cache_path.exists():
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(f".tmp-{id(data)}")
        tmp.write_bytes(data)
        tmp.replace(cache_path)


__all__ = ["BlobCAS"]
