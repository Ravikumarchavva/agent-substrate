"""Documents — the catalog of ingested documents and their chunks, kept in the store's database.

``Documents(store)`` is the ``DocumentStore`` (``documents/protocols.py``). A document and its chunks are saved in one
transaction, so a document is never listed without its chunks and a crash never leaves chunks of a document that was not
saved. It lives in ``documents`` rather than ``stores`` because the document types do, and ``stores`` sits below them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from substrate.documents.types import DocumentChunk, DocumentMetadata
from substrate.stores.database import Tx
from substrate.stores.store import Store

T = TypeVar("T")

SCHEMA = [
    """
CREATE TABLE IF NOT EXISTS documents (
    seq {pk},
    id TEXT NOT NULL UNIQUE,
    metadata_json TEXT NOT NULL,
    saved_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS document_chunks (
    document_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    chunk_json TEXT NOT NULL,
    PRIMARY KEY (document_id, position)
);
"""
]


class Documents:
    """The ``DocumentStore`` of a ``Store``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        await self._store.ensure("documents", SCHEMA)
        return await self._store.run(fn)

    async def save_document(self, metadata: DocumentMetadata, chunks: Sequence[DocumentChunk]) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute("DELETE FROM document_chunks WHERE document_id = ?", metadata.id)
            for position, chunk in enumerate(chunks):
                await tx.execute(
                    "INSERT INTO document_chunks (document_id, position, chunk_json) VALUES (?, ?, ?)",
                    metadata.id,
                    position,
                    json.dumps(chunk.model_dump(mode="json")),
                )
            await tx.execute(
                "INSERT INTO documents (id, metadata_json, saved_at) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET metadata_json = excluded.metadata_json",
                metadata.id,
                json.dumps(metadata.model_dump(mode="json")),
                time.time(),
            )

        await self._run(op)

    async def get_document(self, document_id: str) -> DocumentMetadata | None:
        async def op(tx: Tx) -> DocumentMetadata | None:
            row = await tx.fetchone("SELECT metadata_json FROM documents WHERE id = ?", document_id)
            return DocumentMetadata.model_validate(json.loads(row["metadata_json"])) if row else None

        return await self._run(op)

    async def get_chunks(self, document_id: str) -> Sequence[DocumentChunk]:
        async def op(tx: Tx) -> list[DocumentChunk]:
            rows = await tx.fetchall(
                "SELECT chunk_json FROM document_chunks WHERE document_id = ? ORDER BY position", document_id
            )
            return [DocumentChunk.model_validate(json.loads(row["chunk_json"])) for row in rows]

        return await self._run(op)

    async def list_documents(self, *, limit: int = 100, offset: int = 0) -> Sequence[DocumentMetadata]:
        """Oldest first."""

        async def op(tx: Tx) -> list[DocumentMetadata]:
            rows = await tx.fetchall("SELECT metadata_json FROM documents ORDER BY saved_at, seq LIMIT ? OFFSET ?", limit, offset)
            return [DocumentMetadata.model_validate(json.loads(row["metadata_json"])) for row in rows]

        return await self._run(op)

    async def delete_document(self, document_id: str) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute("DELETE FROM document_chunks WHERE document_id = ?", document_id)
            await tx.execute("DELETE FROM documents WHERE id = ?", document_id)

        await self._run(op)


__all__ = ["SCHEMA", "Documents"]
