"""Vectors — document chunks and their embeddings, kept in the store's database.

``Vectors`` is the one implementation of ``VectorStore`` (``stores/vector.py``) for the folder store. A chunk is a row:
its content and metadata, the words of its text (indexed for full-text search), and its embedding packed as
little-endian float32 with its length and norm beside it.

Search is *exact*: every candidate in the collection is scored against the query, so the answer is never an
approximation of the best one. That costs time proportional to the collection — measured, about 70 ms per
1,000 chunks of 1,536 dimensions (350 ms for 5,000) — fine for a user's documents and a conversation's worth of
retrieval, slow for hundreds of thousands of chunks, which is what an approximate index (rebuilt from these rows, kept
under ``index/``) or the PostgreSQL backend's pgvector are for. On PostgreSQL the embedding is a pgvector column, the
distance is computed in the database, and an HNSW index (one per vector width, ``halfvec`` above 2,000 dimensions) is
created the first time a width is stored, so search is approximate-nearest at scale. Beyond ``search`` it offers what retrieval
pipelines use: ``lexical_search`` (full text, BM25) and ``hybrid_search`` (both, fused by Reciprocal Rank Fusion, which
looks only at rank positions so the two scores never have to be made comparable).

A collection holds vectors of one width — and, when the writer names it (``space=``), of one embedder: adding or searching with another
raises ``VectorSpaceError`` rather than returning a silent zero score. A document may be stored **without** an embedding (``dims`` 0, nothing
for dense search to compare, found by its words): the embedder was down when it was written, or there is none. ``unembedded`` lists them so
they can be embedded later. Above 10,000 documents in a collection the exact scan is slow; a warning says so.
"""

from __future__ import annotations

import heapq
import json
import logging
import math
import sys
from array import array
from collections.abc import Awaitable, Callable
from operator import mul
from typing import TYPE_CHECKING, Any, TypeVar

from substrate.stores import textsearch
from substrate.stores.database import Database, Row, Tx, under
from substrate.stores.vector import Document, SearchResult
from substrate.types.errors import VectorSpaceError

if TYPE_CHECKING:
    from substrate.stores.store import Store

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SCAN_WARN_ROWS = 10_000


def _vector_schema(database: Database) -> str:
    pg = database.dialect == "postgresql"
    return (
        ("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;\n" if pg else "")
        + f"""
CREATE TABLE IF NOT EXISTS vector_docs (
    seq {{pk}},
    collection TEXT NOT NULL,
    id TEXT NOT NULL,
    text TEXT NOT NULL,
    document_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    embedding {"vector" if pg else "BLOB"} NOT NULL,
    dims INTEGER NOT NULL,
    norm DOUBLE PRECISION NOT NULL,
    UNIQUE (collection, id)
);
"""
        + textsearch.ddl(database.dialect, index="vector_fts", table="vector_docs")
    )


def _spaces_schema(database: Database) -> str:
    """Migration 2: which embedder a collection's vectors came from, and (PostgreSQL) a document may have no embedding."""
    return """
CREATE TABLE IF NOT EXISTS vector_spaces (
    collection TEXT PRIMARY KEY,
    space TEXT NOT NULL,
    dims INTEGER NOT NULL
);
""" + (
        "ALTER TABLE vector_docs ALTER COLUMN embedding DROP NOT NULL;\n"
        if database.dialect == "postgresql"
        else ""
    )


SCHEMA = [_vector_schema, _spaces_schema]

# Every column but the embedding (and, on PostgreSQL, the search vector): what a search result is made from.
_LIGHT = "{a}.seq, {a}.collection, {a}.id, {a}.text, {a}.document_json, {a}.metadata_json, {a}.dims, {a}.norm"
_ANN_MAX_DIMS = 4000  # pgvector's HNSW limit for halfvec
_HALF_ABOVE = 2000  # and for vector: wider ones are indexed at half precision

_BIG_ENDIAN = sys.byteorder == "big"


def _pack(vector: list[float]) -> bytes:
    packed = array("f", vector)
    if _BIG_ENDIAN:
        packed.byteswap()
    return packed.tobytes()


def _unpack(blob: bytes) -> array:
    vector = array("f")
    vector.frombytes(bytes(blob))
    if _BIG_ENDIAN:
        vector.byteswap()
    return vector


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def _vector_text(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _embedding(row: Row) -> list[float] | None:
    if not row["dims"]:
        return None  # stored without one
    if "embedding_text" in row.keys():  # PostgreSQL: pgvector's text form, "[0.1,0.2]"
        return [float(x) for x in row["embedding_text"].strip("[]").split(",") if x]
    return list(_unpack(row["embedding"]))


def _document(row: Row) -> Document:
    data = json.loads(row["document_json"])
    return Document(
        id=row["id"],
        content=data["content"],
        metadata=data["metadata"],
        embedding=_embedding(row),
    )


def _result(row: Row, score: float) -> SearchResult:
    data = json.loads(row["document_json"])
    return SearchResult(
        id=row["id"], content=data["content"], score=score, metadata=data["metadata"]
    )


def _matches(row: Row, filter: dict[str, Any] | None) -> bool:
    if not filter:
        return True
    metadata = json.loads(row["metadata_json"])
    return all(metadata.get(key) == value for key, value in filter.items())


def _filter_sql(filter: dict[str, Any] | None) -> tuple[str, list[str]]:
    """PostgreSQL: the metadata filter as JSON containment (every key equal to its value)."""
    if not filter:
        return "", []
    return " AND d.metadata_json::jsonb @> ?::text::jsonb", [json.dumps(filter)]


class Vectors:
    """The ``VectorStore`` of a ``Store``: ``store.vectors``."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._warned: set[str] = set()

    @property
    def store(self) -> Store:
        """The store these vectors live in — for a host that opened it and has to close it."""
        return self._store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        return await self._store.run(fn)

    @property
    def _pg(self) -> bool:
        return self._store.database.dialect == "postgresql"

    async def _ensure_index(self, dims: int) -> None:
        """PostgreSQL: the HNSW index for vectors of this width, created the first time one is stored."""
        if (
            not self._pg
            or dims > _ANN_MAX_DIMS
            or (name := f"vector_ann_{dims}") in self._store._ensured
        ):
            return
        kind, ops = (
            ("halfvec", "halfvec_cosine_ops")
            if dims > _HALF_ABOVE
            else ("vector", "vector_cosine_ops")
        )
        await self._store.database.script(
            f"CREATE INDEX IF NOT EXISTS {name} ON vector_docs USING hnsw ((embedding::{kind}({dims})) {ops}) WHERE dims = {dims}"
        )
        self._store._ensured.add(name)

    # ── write ────────────────────────────────────────────────────────────────

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
        space: str | None = None,
    ) -> list[str]:
        """Insert documents that are not there yet; one already stored under the same id is left as it is."""
        return await self._write(documents, collection, replace=False, space=space)

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
        space: str | None = None,
    ) -> list[str]:
        """Insert documents, replacing any stored under the same id."""
        return await self._write(documents, collection, replace=True, space=space)

    async def _write(
        self,
        documents: list[Document],
        collection: str,
        *,
        replace: bool,
        space: str | None,
    ) -> list[str]:
        async def op(tx: Tx) -> list[str]:
            known = await tx.fetchone(
                "SELECT dims FROM vector_docs WHERE collection = ? AND dims > 0 LIMIT 1",
                collection,
            )
            width = known["dims"] if known else None
            for doc in documents:
                if doc.embedding is None:
                    continue
                if width is not None and len(doc.embedding) != width:
                    raise VectorSpaceError(
                        f"collection {collection!r} holds {width}-wide vectors; document {doc.id!r} is {len(doc.embedding)} wide",
                        collection=collection,
                    )
                width = len(doc.embedding)
            if space is not None and width is not None:
                await self._check_space(tx, collection, space, width, record=True)
            for doc in documents:
                vector = list(doc.embedding) if doc.embedding is not None else None
                conflict = (
                    "ON CONFLICT (collection, id) DO UPDATE SET text = excluded.text, document_json = excluded.document_json, "
                    "metadata_json = excluded.metadata_json, embedding = excluded.embedding, dims = excluded.dims, norm = excluded.norm"
                    if replace
                    else "ON CONFLICT (collection, id) DO NOTHING"
                )
                dumped = doc.model_dump(mode="json")
                if vector is None:
                    stored: Any = None if self._pg else b""
                else:
                    stored = _vector_text(vector) if self._pg else _pack(vector)
                await tx.execute(
                    "INSERT INTO vector_docs (collection, id, text, document_json, metadata_json, embedding, dims, norm) "
                    f"VALUES (?, ?, ?, ?, ?, {'?::text::vector' if self._pg and vector is not None else '?'}, ?, ?) {conflict}",
                    collection,
                    doc.id,
                    doc.to_text(),
                    json.dumps(
                        {"content": dumped["content"], "metadata": dumped["metadata"]}
                    ),
                    json.dumps(dumped["metadata"]),
                    stored,
                    len(vector) if vector is not None else 0,
                    _norm(vector) if vector is not None else 0.0,
                )
            return [doc.id for doc in documents]

        written = await self._run(op)
        embedded = next(
            (doc.embedding for doc in documents if doc.embedding is not None), None
        )
        if embedded is not None:
            await self._ensure_index(len(embedded))
        return written

    @staticmethod
    async def _check_space(
        tx: Tx, collection: str, space: str, width: int, *, record: bool
    ) -> None:
        row = await tx.fetchone(
            "SELECT space, dims FROM vector_spaces WHERE collection = ?", collection
        )
        if row is None:
            if record:
                await tx.execute(
                    "INSERT INTO vector_spaces (collection, space, dims) VALUES (?, ?, ?)",
                    collection,
                    space,
                    width,
                )
            return
        if row["space"] != space or int(row["dims"]) != width:
            raise VectorSpaceError(
                f"collection {collection!r} holds {row['dims']}-wide vectors from {row['space']!r}; these are {width}-wide from {space!r} — "
                "re-embed the collection with one embedder, or use another collection",
                collection=collection,
            )

    async def space_of(self, collection: str) -> tuple[str, int] | None:
        """``(embedder, width)`` the collection's vectors came from, if a writer named it; ``None`` if it never did."""

        async def op(tx: Tx) -> tuple[str, int] | None:
            row = await tx.fetchone(
                "SELECT space, dims FROM vector_spaces WHERE collection = ?", collection
            )
            return (row["space"], int(row["dims"])) if row else None

        return await self._run(op)

    async def unembedded(
        self, *, collection: str = "default", limit: int = 500
    ) -> list[Document]:
        """Documents stored without an embedding (the embedder was unavailable), oldest first, ``limit`` at a time."""

        async def op(tx: Tx) -> list[Document]:
            rows = await tx.fetchall(
                f"SELECT {_LIGHT.format(a='d')} FROM vector_docs d WHERE d.collection = ? AND d.dims = 0 ORDER BY d.seq LIMIT ?",
                collection,
                limit,
            )
            return [_document(row) for row in rows]

        return await self._run(op)

    async def delete_where(
        self, *, collection: str = "default", filter: dict[str, Any]
    ) -> int:
        """Delete the documents of ``collection`` whose metadata has every key of ``filter`` equal to its value. Returns the count."""

        async def op(tx: Tx) -> int:
            rows = await tx.fetchall(
                "SELECT id, metadata_json FROM vector_docs WHERE collection = ?",
                collection,
            )
            ids = [row["id"] for row in rows if _matches(row, filter)]
            for doc_id in ids:
                await tx.execute(
                    "DELETE FROM vector_docs WHERE collection = ? AND id = ?",
                    collection,
                    doc_id,
                )
            return len(ids)

        return await self._run(op)

    async def delete(self, ids: list[str], *, collection: str = "default") -> int:
        async def op(tx: Tx) -> int:
            removed = 0
            for doc_id in ids:
                removed += await tx.execute(
                    "DELETE FROM vector_docs WHERE collection = ? AND id = ?",
                    collection,
                    doc_id,
                )
            return removed

        return await self._run(op)

    # ── read ─────────────────────────────────────────────────────────────────

    async def get(
        self, ids: list[str], *, collection: str = "default"
    ) -> list[Document]:
        async def op(tx: Tx) -> list[Document]:
            found: list[Document] = []
            for doc_id in ids:
                row = await tx.fetchone(
                    f"SELECT {_LIGHT.format(a='d')}{', d.embedding::text AS embedding_text' if self._pg else ', d.embedding'} "
                    "FROM vector_docs d WHERE d.collection = ? AND d.id = ?",
                    collection,
                    doc_id,
                )
                if row is not None:
                    found.append(_document(row))
            return found

        return await self._run(op)

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
        space: str | None = None,
    ) -> list[SearchResult]:
        """The ``limit`` documents whose embeddings are closest (cosine) to ``query_embedding``, best first. ``space`` names the embedder
        the query came from: a collection written by another is refused (``VectorSpaceError``), not searched."""

        async def op(tx: Tx) -> list[SearchResult]:
            if space is not None:
                await self._check_space(
                    tx, collection, space, len(query_embedding), record=False
                )
            return [
                _result(row, score)
                for score, row in await self._rank(
                    tx, query_embedding, collection, limit, filter
                )
            ]

        return await self._run(op)

    async def _rank(
        self,
        tx: Tx,
        query: list[float],
        collection: str,
        limit: int,
        filter: dict[str, Any] | None,
    ) -> list[tuple[float, Row]]:
        if self._pg:
            return await self._rank_pg(tx, query, collection, limit, filter)
        q = array("f", query)
        q_norm = _norm(query)
        rows = await tx.fetchall(
            "SELECT * FROM vector_docs WHERE collection = ? AND dims > 0 ORDER BY seq",
            collection,
        )
        if len(rows) > _SCAN_WARN_ROWS and collection not in self._warned:
            self._warned.add(collection)
            logger.warning(
                "collection %r has %d embedded documents: the exact scan takes about %d ms per search; use postgres_store (an HNSW index) for collections this large",
                collection, len(rows), len(rows) * len(query) // 25_000,
            )  # fmt: skip

        def scored() -> list[tuple[float, int, Row]]:
            out: list[tuple[float, int, Row]] = []
            for position, row in enumerate(rows):
                if not _matches(row, filter):
                    continue
                if row["dims"] != len(q) or row["norm"] == 0.0 or q_norm == 0.0:
                    # A document of another width, or a zero vector, has no direction to compare: it scores 0 rather
                    # than disappearing, so it can still be returned when nothing is closer.
                    out.append((0.0, -position, row))
                else:
                    out.append(
                        (
                            sum(map(mul, q, _unpack(row["embedding"])))
                            / (q_norm * row["norm"]),
                            -position,
                            row,
                        )
                    )
            return out

        best = heapq.nlargest(limit, scored(), key=lambda item: (item[0], item[1]))
        return [(score, row) for score, _position, row in best]

    async def _rank_pg(
        self,
        tx: Tx,
        query: list[float],
        collection: str,
        limit: int,
        filter: dict[str, Any] | None,
    ) -> list[tuple[float, Row]]:
        """The nearest by cosine, in the database. The distance expression is the HNSW index's own, so the index is used;
        a document with no direction (a zero vector) scores 0, as in the exact search."""
        dims = len(query)
        light = _LIGHT.format(a="d")
        where, filter_params = _filter_sql(filter)
        if _norm(query) == 0.0:
            rows = await tx.fetchall(
                f"SELECT {light} FROM vector_docs d WHERE d.collection = ? AND d.dims = ?{where} ORDER BY d.seq LIMIT ?",
                collection,
                dims,
                *filter_params,
                limit,
            )
            return [(0.0, row) for row in rows]
        kind = "halfvec" if dims > _HALF_ABOVE else "vector"
        distance = f"d.embedding::{kind}({dims}) <=> ?::text::{kind}({dims})"
        text = _vector_text(query)
        await tx.execute(
            "SET LOCAL hnsw.iterative_scan = strict_order"
        )  # keep filling the limit past rows the filter drops
        rows = await tx.fetchall(
            f"SELECT {light}, CASE WHEN d.norm = 0 THEN 0.0 ELSE 1 - ({distance}) END AS score FROM vector_docs d "
            f"WHERE d.collection = ? AND d.dims = {int(dims)}{where} ORDER BY {distance} LIMIT ?",
            text,
            collection,
            *filter_params,
            text,
            limit,
        )
        return [(float(row["score"]), row) for row in rows]

    async def lexical_search(
        self,
        query_text: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
        match: str = "all",
    ) -> list[SearchResult]:
        """Full-text matches, best BM25 first: every word must appear (``match="all"``) or any of them (``"any"``); word forms are
        stemmed, common words and all but the first 16 distinct words are ignored. ``score`` is the raw BM25 value — comparable within
        one query, not across queries."""

        async def op(tx: Tx) -> list[SearchResult]:
            return [
                _result(row, score)
                for row, score in await self._lexical(
                    tx, query_text, collection, limit, filter, match
                )
            ]

        return await self._run(op)

    async def _lexical(
        self,
        tx: Tx,
        query_text: str,
        collection: str,
        limit: int,
        filter: dict[str, Any] | None,
        match_mode: str = "all",
    ) -> list[tuple[Row, float]]:
        query_words = textsearch.terms(query_text)
        if not query_words:
            return []
        dialect = self._store.database.dialect
        source, match, match_params = textsearch.ranked(
            dialect,
            index="vector_fts",
            table="vector_docs",
            alias="d",
            query_words=query_words,
            match=match_mode,
        )
        score = textsearch.score(dialect, index="vector_fts", alias="d")
        if self._pg:
            where, filter_params = _filter_sql(filter)
            rows = await tx.fetchall(
                f"SELECT {_LIGHT.format(a='d')}, {score} AS score FROM {source} WHERE {match} AND d.collection = ?{where} "
                "ORDER BY score DESC, d.seq LIMIT ?",
                *match_params,
                collection,
                *filter_params,
                limit,
            )
            return [(row, float(row["score"])) for row in rows]
        rows = await tx.fetchall(
            f"SELECT d.*, {score} AS score FROM {source} WHERE {match} AND d.collection = ? ORDER BY score DESC, d.seq",
            *match_params,
            collection,
        )
        matched = [(row, float(row["score"])) for row in rows if _matches(row, filter)]
        return matched[:limit]

    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        *,
        collection: str = "default",
        dense_k: int = 50,
        lexical_k: int = 50,
        fused_k: int = 50,
        rrf_k: int = 60,
        filter: dict[str, Any] | None = None,
        space: str | None = None,
    ) -> list[SearchResult]:
        """Dense search and full-text search fused with Reciprocal Rank Fusion: a document's score is the sum of
        ``1 / (rrf_k + rank)`` over the lists it appears in. Only ranks matter, so cosine similarity and BM25 never have
        to be calibrated against each other. The words leg matches **any** word (a question is not a phrase), so a passage the
        embedding misses is still found by the words it shares. ``score`` is that fused score — comparable across results of this
        method, not to a plain ``search``."""

        async def op(tx: Tx) -> list[SearchResult]:
            if space is not None:
                await self._check_space(
                    tx, collection, space, len(query_embedding), record=False
                )
            dense = await self._rank(tx, query_embedding, collection, dense_k, filter)
            lexical = await self._lexical(
                tx, query_text, collection, lexical_k, filter, "any"
            )
            fused: dict[str, tuple[float, Row]] = {}
            for rank, (_score, row) in enumerate(dense, start=1):
                fused[row["id"]] = (1.0 / (rrf_k + rank), row)
            for rank, (row, _score) in enumerate(lexical, start=1):
                prior = fused.get(row["id"], (0.0, row))[0]
                fused[row["id"]] = (prior + 1.0 / (rrf_k + rank), row)
            ranked = sorted(fused.values(), key=lambda item: item[0], reverse=True)
            return [_result(row, score) for score, row in ranked[:fused_k]]

        return await self._run(op)

    # ── collections ──────────────────────────────────────────────────────────

    async def list_collections(self) -> list[str]:
        async def op(tx: Tx) -> list[str]:
            return [
                row["collection"]
                for row in await tx.fetchall(
                    "SELECT DISTINCT collection FROM vector_docs ORDER BY collection"
                )
            ]

        return await self._run(op)

    async def delete_collection(self, collection: str) -> int:
        async def op(tx: Tx) -> int:
            await tx.execute(
                "DELETE FROM vector_spaces WHERE collection = ?", collection
            )
            return await tx.execute(
                "DELETE FROM vector_docs WHERE collection = ?", collection
            )

        return await self._run(op)

    async def rename_collection(self, old: str, new: str) -> int:
        """Re-key every document of ``old`` to ``new``; a document whose id is already in ``new`` is replaced."""

        async def op(tx: Tx) -> int:
            await tx.execute(
                "DELETE FROM vector_docs WHERE collection = ? AND id IN (SELECT id FROM vector_docs WHERE collection = ?)",
                new,
                old,
            )
            moved = await tx.execute(
                "UPDATE vector_docs SET collection = ? WHERE collection = ?", new, old
            )
            await tx.execute("DELETE FROM vector_spaces WHERE collection = ?", new)
            await tx.execute(
                "UPDATE vector_spaces SET collection = ? WHERE collection = ?", new, old
            )
            return moved

        return await self._run(op)

    async def erase_under(self, name: str) -> int:
        """Delete every collection named ``name`` or below it. Returns the documents removed."""
        clause, params = under("collection", name)

        async def op(tx: Tx) -> int:
            erased = await tx.execute(
                f"DELETE FROM vector_docs WHERE {clause}", *params
            )
            await tx.execute(f"DELETE FROM vector_spaces WHERE {clause}", *params)
            if erased and (
                compact := textsearch.compact(
                    self._store.database.dialect, index="vector_fts"
                )
            ):
                # The delete trigger only marks index entries deleted; rewriting the index drops the words themselves.
                await tx.execute(compact)
            return erased

        erased = await self._run(op)
        if erased:
            await self._store.database.reclaim()
        return erased


__all__ = ["SCHEMA", "Vectors"]
