"""`DataLibrary`: the `DocumentLibrary` contract over Loamz Data's Documents domain.

The data service runs the `Library` engine (catalog, vectors, rerank) on PostgreSQL behind grants
and tenancy; this process holds none of that.
"""

from __future__ import annotations

from typing import Any

from loamz.data.documents import collection_id
from loamz.data.errors import NotFoundError as ClientNotFoundError
from loamz.engine.agents.data._connection import (
    DataServiceConnection,
    TokenProvider,
    identity_of,
    mint,
)
from loamz.engine.agents.data._wire import from_wire
from loamz.engine.documents.protocols import DocumentLibrary
from loamz.engine.documents.types import (
    Added,
    DocumentBrief,
    DocumentInfo,
    Enricher,
    EnrichmentUsage,
    ExtractionResult,
    Hits,
    Listing,
    Outline,
    Passage,
    Picture,
    Topic,
)


class DataLibrary(DocumentLibrary):
    """Document library backed by the Loamz Data document service."""

    def __init__(self, connection: DataServiceConnection, tokens: TokenProvider) -> None:
        self._connection = connection
        self._tokens = tokens

    @property
    def file_topics(self) -> bool:
        return True

    async def _token(self, collection: str) -> str:
        tenant, user = identity_of(collection)
        return await mint(self._tokens, tenant, user)

    # ── reads ────────────────────────────────────────────────────────────
    async def info(self, collection: str, document: str) -> DocumentInfo | None:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.get(cid, document)
                return from_wire(DocumentInfo, raw) if raw else None
            except ClientNotFoundError:
                return None

    async def list(self, *, collection: str, limit: int = 50, after: str | None = None) -> Listing:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.list(cid, limit=limit, after=after)
                return from_wire(Listing, raw)
            except ClientNotFoundError:
                return Listing(documents=(), next=None)

    async def collections(self, *, under: str) -> list[tuple[str, int]]:
        tenant = (
            under.removeprefix("tenants/").split("/")[0]
            if under.startswith("tenants/")
            else "default"
        )
        token = await mint(self._tokens, tenant, None)
        with self._connection.bearer(token):
            res = await self._connection.client.documents.collections(under=under)
            return [(c["name"], c["document_count"]) for c in res]

    async def outline(
        self, *, collection: str, document: str, section: int | None = None
    ) -> Outline | None:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.outline(
                    cid, document, section=section
                )
                return from_wire(Outline, raw) if raw else None
            except ClientNotFoundError:
                return None

    async def read(
        self,
        *,
        collection: str,
        document: str,
        section: int | None = None,
        pages: tuple[int, int] | None = None,
        offset: int = 0,
    ) -> Passage | None:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.read(
                    cid, document, section=section, pages=pages, offset=offset
                )
                return from_wire(Passage, raw) if raw else None
            except ClientNotFoundError:
                return None

    async def find(
        self,
        *,
        collection: str,
        query: str,
        document: str | None = None,
        limit: int = 8,
    ) -> Hits:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.search(
                    cid, query, document=document, limit=limit
                )
                return from_wire(Hits, raw)
            except ClientNotFoundError:
                return Hits()

    async def view(
        self,
        *,
        collection: str,
        image: str | None = None,
        document: str | None = None,
        page: int | None = None,
    ) -> Picture | None:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                img = await self._connection.client.documents.image(
                    cid, image=image, document=document, page=page
                )
                if not img:
                    return None
                return Picture(
                    data=img.data,
                    media_type=img.media_type,
                    document=img.document,
                    image=img.image,
                    page=img.page,
                    label=img.label,
                    caption=img.caption,
                )
            except ClientNotFoundError:
                return None

    async def browse(self, *, collection: str, topic: str = "") -> Topic | None:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.topics(cid, topic=topic)
                return from_wire(Topic, raw) if raw else None
            except ClientNotFoundError:
                return None

    # ── writes ───────────────────────────────────────────────────────────
    async def add(
        self,
        source: bytes | ExtractionResult,
        filename: str = "",
        *,
        collection: str,
        resource: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        sha256: str | None = None,
    ) -> Added:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            await self._connection.client.documents.ensure(collection, semantic=True)
            if isinstance(source, bytes):
                res = await self._connection.client.documents.add(
                    cid,
                    filename=filename,
                    data=source,
                    content_type=content_type,
                    resource=resource,
                    metadata=metadata,
                    sha256=sha256,
                )
            else:
                res = await self._connection.client.documents.add(
                    cid,
                    filename=filename,
                    extraction=source.model_dump(mode="json"),
                    content_type=content_type,
                    resource=resource,
                    metadata=metadata,
                    sha256=sha256,
                )
            return from_wire(Added, res)

    async def delete(self, *, collection: str, document: str) -> bool:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                await self._connection.client.documents.delete(cid, document)
                return True
            except ClientNotFoundError:
                return False

    async def erase_under(self, prefix: str) -> int:
        tenant = (
            prefix.removeprefix("tenants/").split("/")[0]
            if prefix.startswith("tenants/")
            else "default"
        )
        token = await mint(self._tokens, tenant, None)
        with self._connection.bearer(token):
            matching = await self._connection.client.documents.collections(under=prefix)
            count = 0
            for item in matching:
                try:
                    await self._connection.client.documents.delete_collection(item["id"])
                    count += item.get("document_count", 0)
                except ClientNotFoundError:
                    pass
            return count

    async def reindex(self, *, collection: str, missing_only: bool = False) -> int:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            return await self._connection.client.documents.reindex(cid, missing_only=missing_only)

    async def embed_missing(self, *, collection: str) -> int:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            return await self._connection.client.documents.embed_missing(cid)

    # ── enrichment ───────────────────────────────────────────────────────
    async def pending(
        self, *, collection: str, enricher: Enricher | None = None, limit: int = 50
    ) -> list[str]:
        token = await self._token(collection)
        cid = collection_id(collection)
        ed = {"name": enricher.name, "version": enricher.version} if enricher else None
        with self._connection.bearer(token):
            try:
                return await self._connection.client.documents.pending(
                    cid, enricher=ed, limit=limit
                )
            except ClientNotFoundError:
                return []

    async def pending_all(
        self,
        *,
        enricher: Enricher | None = None,
        limit: int = 100,
        matching: str = "%",
        excluding: str | None = None,
    ) -> list[tuple[str, str]]:
        tenant = "default"
        token = await mint(self._tokens, tenant, None)
        ed = {"name": enricher.name, "version": enricher.version} if enricher else None
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.pending_all(
                    matching, excluding=excluding, enricher=ed, limit=limit
                )
                return [(item["collection"], item["document"]) for item in raw]
            except ClientNotFoundError:
                return []

    async def needs_reorganising(self, collection: str) -> bool:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            try:
                res = await self._connection.client.documents.get(cid, "index")
                return bool(res and res.get("needs_reorganising"))
            except ClientNotFoundError:
                return False

    async def claim(
        self, *, collection: str, document: str, enricher: Enricher
    ) -> DocumentBrief | None:
        token = await self._token(collection)
        cid = collection_id(collection)
        ed = {"name": enricher.name, "version": enricher.version}
        with self._connection.bearer(token):
            try:
                raw = await self._connection.client.documents.claim(cid, document, enricher=ed)
                return from_wire(DocumentBrief, raw) if raw else None
            except ClientNotFoundError:
                return None

    async def annotate(
        self,
        *,
        collection: str,
        document: str,
        enricher: Enricher,
        description: str,
        sections: list[str] | None = None,
        topics: list[str] | None = None,
        usage: EnrichmentUsage | None = None,
    ) -> None:
        token = await self._token(collection)
        cid = collection_id(collection)
        ed = {"name": enricher.name, "version": enricher.version}
        u = usage.model_dump(mode="json") if usage else None
        with self._connection.bearer(token):
            await self._connection.client.documents.annotate(
                cid,
                document,
                enricher=ed,
                description=description,
                sections=sections or [],
                topics=topics or [],
                usage=u,
            )

    async def fail(self, *, collection: str, document: str, enricher: Enricher, error: str) -> None:
        token = await self._token(collection)
        cid = collection_id(collection)
        ed = {"name": enricher.name, "version": enricher.version}
        with self._connection.bearer(token):
            await self._connection.client.documents.fail(cid, document, enricher=ed, error=error)

    async def set_topics(self, *, collection: str, plan: dict[str, list[str]], by: str) -> None:
        token = await self._token(collection)
        cid = collection_id(collection)
        with self._connection.bearer(token):
            await self._connection.client.documents.set_topics(cid, plan=plan, by=by)
