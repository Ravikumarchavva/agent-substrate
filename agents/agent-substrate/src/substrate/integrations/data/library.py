"""``DataLibrary``: the ``DocumentLibrary`` contract over substrate-data's Documents domain (ADR 0004 and 0008).

The data service runs the ``Library`` engine (catalog, vectors, rerank) on PostgreSQL behind grants and tenancy, keeps the OKF bundle in
the tenant's volume, and reads uploads with its document server; this process holds none of that. A collection is the same key prefix
the folder ``Library`` uses (``tenants/{tenant}/users/{user}/conversations/{c}/documents``, ``tenants/{tenant}/knowledge/{kb}/library``),
so the platform's collection helpers work unchanged and the tenant and the owning user are read from the name.

Reads hide existence (a collection of another user, or none, is empty/``None``), as the contract asks. The service embeds and reranks on
the server; one that is down degrades ``find`` to words and says so in ``Hits.note``. LLM enrichment stays with the caller (ADR 0004): the
enrichment methods only claim, record and annotate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from substrate_data_client import DataError, Forbidden, InsufficientScope, NotFound
from substrate_data_client.documents import collection_id

from substrate.documents.types import (
    Added,
    DocumentBrief,
    DocumentError,
    DocumentInfo,
    Enricher,
    EnrichmentState,
    EnrichmentUsage,
    ExtractionResult,
    Hit,
    Hits,
    Listing,
    Outline,
    Passage,
    Picture,
    Topic,
)
from substrate.integrations.data._connection import (
    DocumentTokens,
    DataServiceConnection,
    identity_of,
    mint,
)
from substrate.integrations.data._errors import raise_for
from substrate.integrations.data._wire import from_wire
from substrate.stores.files import WorkspacePathError
from substrate.types.errors import ObjectNotFoundError, VectorSpaceError


def _enricher(value: Enricher) -> dict[str, str]:
    return {"name": value.name, "version": value.version}


def _hidden(exc: DataError) -> bool:
    """A read of something that is not there, or is not the caller's, is simply empty."""
    return isinstance(exc, (NotFound, Forbidden)) and not isinstance(
        exc, InsufficientScope
    )


class DataLibrary:
    """A ``DocumentLibrary`` over substrate-data.

    ``tokens(tenant, user)`` mints the delegated token a call carries (``documents:write`` covers reads). ``semantic`` makes the
    collections this library creates embedded (knowledge bases); ``file_topics`` is the library's answer to ``DocumentLibrary.file_topics``.
    """

    def __init__(
        self,
        connection: DataServiceConnection,
        tokens: DocumentTokens,
        *,
        semantic: bool = False,
        file_topics: bool = False,
    ) -> None:
        self._connection = connection
        self._tokens = tokens
        self._semantic = semantic
        self._file_topics = file_topics
        self._ensured: set[str] = set()

    @property
    def file_topics(self) -> bool:
        return self._file_topics

    # ------------------------------------------------------------------ plumbing
    async def _call(
        self, collection: str, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        """``client.documents.<method>(*args)`` as the identity named by ``collection``."""
        try:
            tenant, user = identity_of(collection)
        except ValueError as exc:
            raise WorkspacePathError(str(exc)) from exc
        token = await mint(self._tokens, tenant, user)
        with self._connection.bearer(token):
            return await getattr(self._connection.client.documents, method)(
                *args, **kwargs
            )

    async def _id(self, collection: str, *, create: bool = False) -> str:
        collection = collection.strip("/")
        if create and collection not in self._ensured:
            try:
                made = await self._call(
                    collection, "ensure", collection, semantic=self._semantic
                )
            except DataError as exc:
                raise_for(exc, collection)
            self._ensured.add(collection)
            return made["id"]
        return collection_id(collection)

    async def _read(
        self, collection: str, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        """A read: ``None`` when there is no such collection or document (or it is not the caller's)."""
        collection = collection.strip("/")
        try:
            return await self._call(
                collection, method, await self._id(collection), *args, **kwargs
            )
        except DataError as exc:
            if _hidden(exc):
                return None
            raise_for(exc, collection)

    async def _write(
        self, collection: str, method: str, *args: Any, **kwargs: Any
    ) -> Any:
        collection = collection.strip("/")
        cid = await self._id(collection, create=True)
        try:
            return await self._call(collection, method, cid, *args, **kwargs)
        except DataError as exc:
            if exc.code == "unreadable-document":
                raise DocumentError(exc.detail) from exc
            if exc.code == "vector-space-mismatch":
                raise VectorSpaceError(exc.detail) from exc
            raise_for(exc, collection)

    # ------------------------------------------------------------------ reads
    async def info(self, collection: str, document: str) -> DocumentInfo | None:
        found = await self._read(collection, "get", document)
        return from_wire(DocumentInfo, found) if found else None

    async def list(
        self, *, collection: str, limit: int = 50, after: str | None = None
    ) -> Listing:
        found = await self._read(collection, "list", limit=limit, after=after)
        return from_wire(Listing, found) if found else Listing(())

    async def collections(self, *, under: str) -> list[tuple[str, int]]:
        under = under.strip("/")
        try:
            found = await self._call(under, "collections", under)
        except DataError as exc:
            if _hidden(exc):
                return []
            raise_for(exc, under)
        return [(c["name"], int(c["documents"])) for c in found if c["documents"]]

    async def outline(
        self, *, collection: str, document: str, section: int | None = None
    ) -> Outline | None:
        found = await self._read(collection, "outline", document, section=section)
        return from_wire(Outline, found) if found else None

    async def read(
        self,
        *,
        collection: str,
        document: str,
        section: int | None = None,
        pages: tuple[int, int] | None = None,
        offset: int = 0,
    ) -> Passage | None:
        found = await self._read(
            collection, "read", document, section=section, pages=pages, offset=offset
        )
        return from_wire(Passage, found) if found else None

    async def find(
        self,
        *,
        collection: str,
        query: str,
        document: str | None = None,
        limit: int = 8,
    ) -> Hits:
        found = await self._read(
            collection, "search", query, document=document, limit=limit
        )
        hits = Hits(from_wire(Hit, h) for h in (found or {}).get("hits", []))
        hits.note = (found or {}).get("note")
        return hits

    async def view(
        self,
        *,
        collection: str,
        image: str | None = None,
        document: str | None = None,
        page: int | None = None,
    ) -> Picture | None:
        found = await self._read(
            collection, "image", image=image, document=document, page=page
        )
        if found is None:
            return None
        return Picture(
            found.data,
            found.media_type,
            found.document,
            found.image,
            found.page,
            found.label,
            found.caption,
        )

    async def browse(self, *, collection: str, topic: str = "") -> Topic | None:
        found = await self._read(collection, "topics", topic)
        return from_wire(Topic, found) if found else None

    # ------------------------------------------------------------------ writes
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
        extra: dict[str, Any] = {
            "content_type": content_type,
            "resource": resource,
            "metadata": metadata,
            "sha256": sha256,
        }
        if isinstance(source, ExtractionResult):
            extra["extraction"] = json.loads(source.model_dump_json())
        else:
            extra["data"] = bytes(source)
        return from_wire(Added, await self._write(collection, "add", filename, **extra))

    async def delete(self, *, collection: str, document: str) -> bool:
        collection = collection.strip("/")
        try:
            await self._call(collection, "delete", await self._id(collection), document)
        except DataError as exc:
            if _hidden(exc):
                return False
            raise_for(exc, collection)
        return True

    async def erase_under(self, prefix: str) -> int:
        """Remove every collection at or under ``prefix`` that the caller may reach; returns the documents removed."""
        prefix = prefix.strip("/")
        removed = 0
        try:
            found = await self._call(prefix, "collections", prefix)
        except DataError as exc:
            if _hidden(exc):
                return 0
            raise_for(exc, prefix)
        for item in found:
            try:
                done = await self._call(item["name"], "delete_collection", item["id"])
            except DataError as exc:
                if _hidden(exc):
                    continue
                raise_for(exc, item["name"])
            removed += int((done or {}).get("documents_removed", 0))
            self._ensured.discard(item["name"])
        return removed

    async def reindex(self, *, collection: str, missing_only: bool = False) -> int:
        return int(await self._write(collection, "reindex", missing_only=missing_only))

    async def embed_missing(self, *, collection: str) -> int:
        return int(await self._write(collection, "embed_missing"))

    # ------------------------------------------------------------------ enrichment
    async def pending(
        self, *, collection: str, enricher: Enricher | None = None, limit: int = 50
    ) -> list[str]:
        found = await self._read(
            collection,
            "pending",
            enricher=_enricher(enricher) if enricher else None,
            limit=limit,
        )
        return list(found or [])

    async def pending_all(
        self,
        *,
        enricher: Enricher | None = None,
        limit: int = 100,
        matching: str = "%",
        excluding: str | None = None,
    ) -> list[tuple[str, str]]:
        stem = matching.split("%", 1)[0].strip("/")
        try:
            found = await self._call(
                stem, "pending_all", matching,
                excluding=excluding, enricher=_enricher(enricher) if enricher else None, limit=limit,
            )  # fmt: skip
        except DataError as exc:
            if _hidden(exc):
                return []
            raise_for(exc, matching)
        return [(item["collection"], item["document"]) for item in found]

    async def needs_reorganising(self, collection: str) -> bool:
        return bool(await self._read(collection, "needs_reorganising"))

    async def brief(self, *, collection: str, document: str) -> DocumentBrief | None:
        found = await self._read(collection, "brief", document)
        return from_wire(DocumentBrief, found) if found else None

    async def enrichment_state(
        self, *, collection: str, document: str
    ) -> EnrichmentState | None:
        found = await self._read(collection, "enrichment_state", document)
        return from_wire(EnrichmentState, found) if found else None

    async def claim(
        self, *, collection: str, document: str, enricher: Enricher, attempts: int
    ) -> None:
        await self._write(collection, "claim", document, _enricher(enricher), attempts)

    async def fail(
        self,
        *,
        collection: str,
        document: str,
        enricher: Enricher,
        attempts: int,
        error: str,
    ) -> None:
        await self._write(
            collection, "fail", document, _enricher(enricher), attempts, error
        )

    async def topic_tree(self, *, collection: str) -> list[str]:
        return list(await self._read(collection, "topic_tree") or [])

    async def annotate(
        self,
        *,
        collection: str,
        document: str,
        card: str,
        sections: Mapping[int, str],
        topics: Sequence[str],
        enricher: Enricher,
        attempts: int,
        usage: EnrichmentUsage,
    ) -> None:
        try:
            await self._write(
                collection, "annotate", document,
                card=card, sections=dict(sections), topics=list(topics),
                enricher=_enricher(enricher), attempts=attempts,
                usage={
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_usd": usage.cost_usd,
                    "calls": usage.calls,
                },
            )  # fmt: skip
        except ObjectNotFoundError as exc:
            raise DocumentError(
                f"there is no document {collection}/{document}"
            ) from exc

    async def set_topics(
        self, *, collection: str, plan: Mapping[str, Sequence[str]], by: str
    ) -> None:
        await self._write(
            collection, "set_topics", {k: list(v) for k, v in plan.items()}, by
        )


__all__ = ["DataLibrary"]
