"""The Library and Memory engines of the agent library, run behind substrate-data's authorization (ADR 0004 and 0008).

The OKF bundle in the tenant's volume is the source of truth; the catalog (full-text) and the chunk vectors live in the
`data_retrieval` database (PostgreSQL with pgvector in production, a SQLite file in development) and are rebuilt by `reindex`.
Parsing is not done here: uploads are read by a document server (a worker role) or arrive already extracted.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from substrate.documents import Library, Reader
from substrate.documents.types import ExtractionResult
from substrate.models.remote import RemoteEmbedder, RemoteReranker
from substrate.stores import FileStore, Store

from substrate_data.core.models import Settings

logger = logging.getLogger(__name__)

_COLLECTIONS = [
    """
CREATE TABLE IF NOT EXISTS data_collections (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    name TEXT NOT NULL,
    semantic INTEGER NOT NULL,
    created_by TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE (tenant, name)
);
"""
]


def collection_id(name: str) -> str:
    """The stable, opaque id of a collection: what URLs and audit events carry instead of its name."""
    return hashlib.sha256(name.encode()).hexdigest()[:24]


def digest(value: str) -> str:
    """A document id or other name as an audit trail may show it."""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CollectionRow:
    id: str
    tenant: str
    name: str
    semantic: bool


class _NoReader:
    """Stands in when no document server is configured: an upload that needs reading is refused, never parsed here."""

    async def read(self, data: bytes, filename: str, **_: Any) -> ExtractionResult:
        return ExtractionResult(
            success=False,
            error="no document reader is configured (the document server is unavailable)",
            engine="none",
            degraded_from="unreachable",
        )


class DocumentEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        files: FileStore | None = None,
        embedder: Any = None,
        reranker: Any = None,
        reader: Any = None,
    ) -> None:
        """`files` holds the OKF bundles (the tenants' volumes in production; default: the retrieval store's own
        file table, for development and tests). `embedder`/`reranker`/`reader` replace the ones built from settings."""
        self.settings = settings
        self.store = self._open_store(settings)
        self.files = files if files is not None else self.store.files
        self.can_read = reader is not None or bool(
            settings.document_reader_url or settings.documents_parse_inline
        )
        reader = reader or self._reader(settings)
        self.lexical = Library(self.store, files=self.files, reader=reader, file_topics=True)
        self.semantic = self.lexical
        if embedder is None and settings.document_embedding_url:
            token = settings.document_embedding_token.get_secret_value()
            timeout = settings.document_embedding_timeout_seconds
            embedder = RemoteEmbedder(settings.document_embedding_url, api_key=token, timeout=timeout)
            reranker = reranker or RemoteReranker(
                settings.document_embedding_url, api_key=token, timeout=timeout
            )
        if embedder is not None:
            self.semantic = Library(
                self.store,
                files=self.files,
                reader=reader,
                embedder=embedder,
                reranker=reranker,
                file_topics=True,
            )

    @staticmethod
    def _open_store(settings: Settings) -> Store:
        url = settings.retrieval_url.get_secret_value()
        if url:
            from substrate.integrations.database import postgres_store

            return postgres_store(url, files=settings.retrieval_dir, schema=settings.retrieval_schema)
        return Store.at(settings.retrieval_dir)

    @staticmethod
    def _reader(settings: Settings) -> Any:
        if settings.document_reader_url:
            return Reader(
                settings.document_reader_url,
                api_key=settings.document_reader_token.get_secret_value(),
                fallback=False,  # the control plane does not parse, not even when the worker is down
            )
        if settings.documents_parse_inline:
            return Reader(isolate=False)
        return _NoReader()

    def library(self, row: CollectionRow) -> Library:
        return self.semantic if row.semantic else self.lexical

    # ------------------------------------------------------------------ the collection registry
    async def ensure_collection(
        self, tenant: str, name: str, *, semantic: bool, created_by: str
    ) -> tuple[CollectionRow, bool]:
        import time

        await self.store.ensure("data_collections", _COLLECTIONS)
        cid = collection_id(name)

        async def op(tx):
            row = await tx.fetchone("SELECT * FROM data_collections WHERE id = ?", cid)
            if row is not None:
                return row, False
            await tx.execute(
                "INSERT INTO data_collections (id, tenant, name, semantic, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                cid, tenant, name, int(semantic), created_by, time.time(),
            )  # fmt: skip
            return await tx.fetchone("SELECT * FROM data_collections WHERE id = ?", cid), True

        row, created = await self.store.run(op)
        return _row(row), created

    async def collection(self, cid: str) -> CollectionRow | None:
        if not re.fullmatch(r"[0-9a-f]{24}", cid):
            return None
        await self.store.ensure("data_collections", _COLLECTIONS)

        async def op(tx):
            return await tx.fetchone("SELECT * FROM data_collections WHERE id = ?", cid)

        row = await self.store.run(op)
        return _row(row) if row else None

    async def collections_of(self, tenant: str, *, prefix: str | None = None) -> list[CollectionRow]:
        await self.store.ensure("data_collections", _COLLECTIONS)

        async def op(tx):
            return await tx.fetchall(
                "SELECT * FROM data_collections WHERE tenant = ? ORDER BY name", tenant
            )

        rows = [_row(r) for r in await self.store.run(op)]
        if prefix is not None:
            rows = [r for r in rows if r.name == prefix or r.name.startswith(prefix.rstrip("/") + "/")]
        return rows

    async def forget_collections(self, ids: list[str]) -> None:
        if not ids:
            return
        await self.store.ensure("data_collections", _COLLECTIONS)

        async def op(tx):
            for cid in ids:
                await tx.execute("DELETE FROM data_collections WHERE id = ?", cid)

        await self.store.run(op)

    async def aclose(self) -> None:
        await self.store.aclose()


def _row(row: Any) -> CollectionRow:
    return CollectionRow(row["id"], row["tenant"], row["name"], bool(row["semantic"]))
