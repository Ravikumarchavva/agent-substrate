"""LocalFilesystemVectorStore — JSON-file-backed vector store for RAG.

Stores data in a local directory tree (default: ``./data/db/vector``), mirroring
``LocalFilesystemHistoryProvider``'s convention of creating a folder on first
use instead of requiring an external database.

Layout::

    <root>/
      documents/<collection>/<doc_id>.json   — one file per Document (incl. embedding)

``search`` loads every document in the requested collection and ranks them
with the same brute-force cosine similarity as :class:`InMemoryVectorStore` —
fine at local/dev scale; this is a durability upgrade, not an ANN index.

This store is intentionally a drop-in replacement for ``InMemoryVectorStore``
for local dev / experimentation — it does NOT require Postgres/pgvector.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from urllib.parse import unquote

from substrate.agents.storage.fs import atomic_write_json, safe_name
from substrate.agents.storage.vector import cosine_similarity
from substrate.kernel.storage.vector import Document, SearchResult

if TYPE_CHECKING:
    from substrate.kernel.llm import EmbeddingClient


class LocalFilesystemVectorStore:
    """Filesystem-backed VectorStore — stores documents as JSON files.

    Suitable for local development and experimentation without requiring a
    running Postgres/pgvector instance. Mirrors :class:`InMemoryVectorStore`'s
    contract exactly, including embedding-client fallback behavior.

    Args:
        root: Path to the storage root directory. Created automatically on
            first write. Defaults to ``./data/db/vector``.
        embedding_client: Optional embedding provider used to compute a
            document's embedding when it is missing. When ``None`` every
            document must already carry an ``embedding``.
    """

    def __init__(
        self,
        root: str | Path = "./data/db/vector",
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self._root = Path(root)
        self._embedding: EmbeddingClient | None = embedding_client

    # ── Internal helpers ─────────────────────────────────────────────────

    def _doc_path(self, collection: str, doc_id: str) -> Path:
        return self._collection_dir(collection) / f"{safe_name(doc_id)}.json"

    def _collection_dir(self, collection: str) -> Path:
        return self._root / "documents" / safe_name(collection)

    def _save_doc(self, collection: str, doc: Document) -> None:
        atomic_write_json(self._doc_path(collection, doc.id), doc.model_dump(mode="json"))

    def _load_doc(self, collection: str, doc_id: str) -> Document | None:
        p = self._doc_path(collection, doc_id)
        if not p.exists():
            return None
        return Document.model_validate_json(p.read_text(encoding="utf-8"))

    def _load_all_docs(self, collection: str) -> list[Document]:
        coll_dir = self._collection_dir(collection)
        if not coll_dir.exists():
            return []
        docs: list[Document] = []
        for p in coll_dir.glob("*.json"):
            try:
                docs.append(Document.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return docs

    async def _ensure_embedding(self, doc: Document) -> Document:
        """Return *doc* with an embedding, computing one if needed."""
        if doc.embedding is not None:
            return doc
        if self._embedding is None:
            raise ValueError(
                f"Document {doc.id} is missing an embedding and no embedding_client "
                "was provided to LocalFilesystemVectorStore."
            )
        vector: list[float] = await self._embedding.embed_single(doc.to_text())
        return Document(
            content=doc.content,
            id=doc.id,
            embedding=vector,
            metadata=doc.metadata,
        )

    @staticmethod
    def _matches_filter(doc: Document, filter: dict[str, Any] | None) -> bool:
        if not filter:
            return True
        return all(doc.metadata.get(key) == value for key, value in filter.items())

    # ── Write ─────────────────────────────────────────────────────────────

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        ids: list[str] = []
        for doc in documents:
            stored = await self._ensure_embedding(doc)
            if self._load_doc(collection, stored.id) is None:  # add = insert-if-absent
                self._save_doc(collection, stored)
            ids.append(stored.id)
        return ids

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        ids: list[str] = []
        for doc in documents:
            stored = await self._ensure_embedding(doc)
            self._save_doc(collection, stored)  # upsert = insert-or-replace
            ids.append(stored.id)
        return ids

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int:
        removed = 0
        for doc_id in ids:
            p = self._doc_path(collection, doc_id)
            if p.exists():
                p.unlink()
                removed += 1
        return removed

    # ── Read ──────────────────────────────────────────────────────────────

    async def get(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> list[Document]:
        docs = []
        for doc_id in ids:
            doc = self._load_doc(collection, doc_id)
            if doc is not None:
                docs.append(doc)
        return docs

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        scored: list[SearchResult] = []
        for doc in self._load_all_docs(collection):
            if doc.embedding is None or not self._matches_filter(doc, filter):
                continue
            score = cosine_similarity(list(query_embedding), list(doc.embedding))
            scored.append(
                SearchResult(
                    id=doc.id,
                    content=doc.content,
                    score=score,
                    metadata=doc.metadata,
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]

    # ── Collections ───────────────────────────────────────────────────────

    async def list_collections(self) -> list[str]:
        docs_dir = self._root / "documents"
        if not docs_dir.exists():
            return []
        return [unquote(p.name) for p in docs_dir.iterdir() if p.is_dir()]

    async def delete_collection(self, collection: str) -> int:
        coll_dir = self._collection_dir(collection)
        if not coll_dir.exists():
            return 0
        files = list(coll_dir.glob("*.json"))
        count = len(files)
        for p in files:
            p.unlink()
        try:
            coll_dir.rmdir()
        except OSError:
            pass
        return count

    async def rename_collection(self, old: str, new: str) -> int:
        old_dir = self._collection_dir(old)
        if not old_dir.exists():
            return 0
        new_dir = self._collection_dir(new)
        new_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for p in old_dir.glob("*.json"):
            os.replace(p, new_dir / p.name)
            count += 1
        try:
            old_dir.rmdir()
        except OSError:
            pass
        return count


__all__ = ["LocalFilesystemVectorStore"]
