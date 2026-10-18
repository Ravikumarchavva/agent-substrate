"""The local document library: SQL tables (documents, sections with full-text search, topics) over the shared database.

Every statement is keyed by `collection`, which always starts with `tenants/<tenant>/`; `tenant_id` is kept as a column so
erasure and the enrichment sweep never scan another tenant's rows. The originals live in `container.file_store`, written
by the caller; the library keeps only what it read.

TODO(integration): [data] adapter - `loamz.data.storage` documents replace this behind `DocumentLibrary`.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from typing import Any

from loamz.agents.features.knowledge.documents.model import (
    MAX_LIST,
    MAX_OUTLINE,
    MAX_READ_CHARS,
    Added,
    Described,
    DocumentBrief,
    DocumentInfo,
    Hit,
    Hits,
    Listing,
    Outline,
    Passage,
    SectionBrief,
    SectionInfo,
    Topic,
)
from loamz.agents.features.knowledge.documents.reader import read_document
from loamz.data.storage.sql import Database, Row, Tx, migrate, textsearch, under

COMPONENT = "knowledge.documents"


def _schema(database: Database) -> str:
    return (
        """
CREATE TABLE IF NOT EXISTS knowledge_documents (
    seq {pk},
    tenant_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    resource TEXT,
    content_type TEXT NOT NULL,
    engine TEXT NOT NULL,
    pages INTEGER NOT NULL,
    sections INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    description TEXT,
    enrichment TEXT NOT NULL,
    enricher TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    enriched_at DOUBLE PRECISION NOT NULL,
    added_at DOUBLE PRECISION NOT NULL,
    UNIQUE (collection, document)
);
CREATE INDEX IF NOT EXISTS knowledge_documents_tenant ON knowledge_documents (tenant_id, enrichment);
CREATE TABLE IF NOT EXISTS knowledge_sections (
    seq {pk},
    tenant_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    first_page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    tokens INTEGER NOT NULL,
    text TEXT NOT NULL,
    description TEXT,
    UNIQUE (collection, document, position)
);
"""
        + textsearch.ddl(
            database.dialect, index="knowledge_sections_fts", table="knowledge_sections"
        )
        + """
CREATE TABLE IF NOT EXISTS knowledge_topics (
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    topic TEXT NOT NULL,
    PRIMARY KEY (collection, document, topic)
);
"""
    )


SCRIPTS = [_schema]
MAX_ATTEMPTS = 3
STUCK_AFTER_S = 600.0


def tenant_of(collection: str) -> str:
    """The tenant a collection belongs to (`tenants/<tenant>/...`), or `""`."""
    parts = collection.split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "tenants" else ""


def document_id(filename: str, sha256: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", filename.rsplit(".", 1)[0].lower()).strip("-")[:40] or "doc"
    return f"{slug}-{sha256[:6]}"


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _doc(row: Row, topics: tuple[str, ...] = ()) -> DocumentInfo:
    return DocumentInfo(
        document=row["document"],
        title=row["title"],
        filename=row["filename"],
        pages=int(row["pages"]),
        sections=int(row["sections"]),
        engine=row["engine"],
        resource=row["resource"],
        meta=json.loads(row["meta_json"]),
        description=row["description"],
        topics=topics,
        enrichment=row["enrichment"],
    )


def _section(row: Row) -> SectionInfo:
    return SectionInfo(
        position=int(row["position"]),
        title=row["title"],
        heading_path=tuple(json.loads(row["heading_path"])),
        first_page=int(row["first_page"]),
        last_page=int(row["last_page"]),
        tokens=int(row["tokens"]),
        description=row["description"],
    )


class LocalLibrary:
    """`DocumentLibrary` over SQL: text documents only, searched by words (porter-stemmed, ranked)."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _run[T](self, fn: Any) -> T:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True
        async with self._db.transaction() as tx:
            return await fn(tx)

    async def _topics(self, tx: Tx, collection: str, documents: list[str]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = defaultdict(list)
        for document in documents:
            for row in await tx.fetchall(
                "SELECT topic FROM knowledge_topics WHERE collection = ? AND document = ? ORDER BY topic",
                collection,
                document,
            ):
                found[document].append(row["topic"])
        return found

    async def _decorate(self, tx: Tx, collection: str, rows: list[Row]) -> list[DocumentInfo]:
        topics = await self._topics(tx, collection, [r["document"] for r in rows])
        return [_doc(r, tuple(topics.get(r["document"], ()))) for r in rows]

    # ── adding ──────────────────────────────────────────────────────────────
    async def add(
        self,
        data: bytes,
        filename: str,
        *,
        collection: str,
        resource: str | None = None,
        content_type: str | None = None,
        sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Added:
        collection = collection.strip("/")
        digest = sha256 or hashlib.sha256(data).hexdigest()
        document = document_id(filename, digest)
        read = None
        existing = await self.info(collection, document)
        if existing is not None:
            return Added(
                document,
                existing.title,
                existing.filename,
                existing.pages,
                existing.sections,
                duplicate=True,
            )
        read = read_document(data, filename, content_type)
        tenant = tenant_of(collection)

        async def op(tx: Tx) -> None:
            await tx.execute(
                "INSERT INTO knowledge_documents (tenant_id, collection, document, title, filename, resource, content_type, "
                "engine, pages, sections, sha256, meta_json, description, enrichment, enricher, attempts, enriched_at, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'none', '', 0, 0, ?) ON CONFLICT DO NOTHING",
                tenant, collection, document, read.title, filename, resource, content_type or "", read.engine,
                read.pages, len(read.sections), digest, json.dumps(metadata or {}, default=str), time.time(),
            )  # fmt: skip
            for position, section in enumerate(read.sections, start=1):
                await tx.execute(
                    "INSERT INTO knowledge_sections (tenant_id, collection, document, position, title, heading_path, "
                    "first_page, last_page, tokens, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    tenant, collection, document, position, section.title, json.dumps(list(section.heading_path)),
                    section.first_page, section.last_page, _tokens(section.text), section.text,
                )  # fmt: skip

        await self._run(op)
        return Added(document, read.title, filename, read.pages, len(read.sections))

    # ── reading ─────────────────────────────────────────────────────────────
    async def info(self, collection: str, document: str) -> DocumentInfo | None:
        collection = collection.strip("/")

        async def op(tx: Tx) -> DocumentInfo | None:
            row = await tx.fetchone(
                "SELECT * FROM knowledge_documents WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            return None if row is None else (await self._decorate(tx, collection, [row]))[0]

        return await self._run(op)

    async def list(self, *, collection: str, after: str | None = None, limit: int = 50) -> Listing:
        collection = collection.strip("/")
        limit = max(1, min(limit, MAX_LIST))
        cursor = int(after) if after and after.isdigit() else 0

        async def op(tx: Tx) -> Listing:
            rows = await tx.fetchall(
                "SELECT * FROM knowledge_documents WHERE collection = ? AND seq > ? ORDER BY seq LIMIT ?",
                collection, cursor, limit + 1,
            )  # fmt: skip
            more = len(rows) > limit
            rows = rows[:limit]
            docs = await self._decorate(tx, collection, rows)
            return Listing(tuple(docs), str(rows[-1]["seq"]) if more and rows else None)

        return await self._run(op)

    async def outline(
        self, *, collection: str, document: str, section: int | None = None
    ) -> Outline | None:
        collection = collection.strip("/")
        info = await self.info(collection, document)
        if info is None:
            return None

        async def op(tx: Tx) -> Outline | None:
            if section is not None:
                row = await tx.fetchone(
                    "SELECT * FROM knowledge_sections WHERE collection = ? AND document = ? AND position = ?",
                    collection, document, section,
                )  # fmt: skip
                if row is None:
                    return None
                heads = [
                    (len(m.group(1)), m.group(2).strip(), int(row["first_page"]))
                    for m in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", row["text"], re.MULTILINE)
                ]
                return Outline(
                    info,
                    (_section(row),),
                    headings=tuple(heads[:MAX_OUTLINE]),
                    more=max(0, len(heads) - MAX_OUTLINE),
                )
            rows = await tx.fetchall(
                "SELECT * FROM knowledge_sections WHERE collection = ? AND document = ? ORDER BY position LIMIT ?",
                collection, document, MAX_OUTLINE + 1,
            )  # fmt: skip
            return Outline(
                info,
                tuple(_section(r) for r in rows[:MAX_OUTLINE]),
                more=max(0, len(rows) - MAX_OUTLINE),
            )

        return await self._run(op)

    async def read(
        self,
        *,
        collection: str,
        document: str,
        section: int | None = None,
        pages: tuple[int, int] | None = None,
        offset: int = 0,
    ) -> Passage | None:
        collection = collection.strip("/")
        info = await self.info(collection, document)
        if info is None:
            return None

        async def op(tx: Tx) -> Passage | None:
            if section is not None:
                rows = await tx.fetchall(
                    "SELECT * FROM knowledge_sections WHERE collection = ? AND document = ? AND position = ?",
                    collection, document, section,
                )  # fmt: skip
            elif pages is not None:
                rows = await tx.fetchall(
                    "SELECT * FROM knowledge_sections WHERE collection = ? AND document = ? "
                    "AND last_page >= ? AND first_page <= ? ORDER BY position",
                    collection, document, pages[0], pages[1],
                )  # fmt: skip
            else:
                rows = await tx.fetchall(
                    "SELECT * FROM knowledge_sections WHERE collection = ? AND document = ? ORDER BY position LIMIT 1",
                    collection, document,
                )  # fmt: skip
            if not rows:
                return None
            text = "\n\n".join(r["text"] for r in rows)
            start = max(0, offset)
            chunk = text[start : start + MAX_READ_CHARS]
            end = start + len(chunk)
            return Passage(
                info, _section(rows[0]), chunk, offset=start,
                next_offset=end if end < len(text) else None, total_chars=len(text),
            )  # fmt: skip

        return await self._run(op)

    async def find(
        self, *, collection: str, query: str, document: str | None = None, limit: int = 5
    ) -> Hits:
        collection = collection.strip("/")
        words = textsearch.terms(query)
        if not words:
            return Hits()
        dialect = self._db.dialect

        async def op(tx: Tx) -> Hits:
            for mode in ("all", "any"):
                source, where, params = textsearch.ranked(
                    dialect, index="knowledge_sections_fts", table="knowledge_sections",
                    alias="s", query_words=words, match=mode,
                )  # fmt: skip
                score = textsearch.score(dialect, index="knowledge_sections_fts", alias="s")
                sql = (
                    f"SELECT s.*, {score} AS score FROM {source} WHERE {where} AND s.collection = ?"
                )
                args: list[Any] = [*params, collection]
                if document:
                    sql += " AND s.document = ?"
                    args.append(document)
                rows = await tx.fetchall(sql + " ORDER BY score DESC LIMIT ?", *args, limit)
                if not rows:
                    continue
                hits = Hits()
                for row in rows:
                    doc = await tx.fetchone(
                        "SELECT * FROM knowledge_documents WHERE collection = ? AND document = ?",
                        collection, row["document"],
                    )  # fmt: skip
                    if doc is None:
                        continue
                    info = (await self._decorate(tx, collection, [doc]))[0]
                    hits.append(
                        Hit(
                            info,
                            _section(row),
                            textsearch.snippet(row["text"], words),
                            float(row["score"]),
                        )
                    )
                return hits
            return Hits()

        return await self._run(op)

    async def browse(self, *, collection: str, topic: str = "") -> Topic | None:
        collection = collection.strip("/")
        topic = topic.strip("/")

        async def op(tx: Tx) -> Topic | None:
            rows = await tx.fetchall(
                "SELECT topic, document FROM knowledge_topics WHERE collection = ? ORDER BY topic, document",
                collection,
            )
            children: dict[str, set[str]] = defaultdict(set)
            here: list[str] = []
            known = topic == ""
            for r in rows:
                t = r["topic"]
                if topic and t != topic and not t.startswith(topic + "/"):
                    continue
                known = True
                rest = t[len(topic) :].lstrip("/") if topic else t
                if not rest:
                    here.append(r["document"])
                else:
                    children[(topic + "/" if topic else "") + rest.split("/", 1)[0]].add(
                        r["document"]
                    )
            if not known:
                return None
            docs: list[Row] = []
            for document in here[:MAX_LIST]:
                row = await tx.fetchone(
                    "SELECT * FROM knowledge_documents WHERE collection = ? AND document = ?",
                    collection,
                    document,
                )
                if row is not None:
                    docs.append(row)
            return Topic(
                topic,
                tuple(sorted((p, len(d)) for p, d in children.items())),
                tuple(await self._decorate(tx, collection, docs)),
                more=max(0, len(here) - MAX_LIST),
            )

        return await self._run(op)

    # ── removing ────────────────────────────────────────────────────────────
    async def delete(self, *, collection: str, document: str) -> bool:
        collection = collection.strip("/")

        async def op(tx: Tx) -> bool:
            for table in ("knowledge_topics", "knowledge_sections"):
                await tx.execute(
                    f"DELETE FROM {table} WHERE collection = ? AND document = ?",
                    collection,
                    document,
                )
            removed = await tx.execute(
                "DELETE FROM knowledge_documents WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            return removed > 0

        return await self._run(op)

    async def collections(self, *, under: str) -> list[tuple[str, int]]:
        clause, params = _under("collection", under.strip("/"))

        async def op(tx: Tx) -> list[tuple[str, int]]:
            rows = await tx.fetchall(
                f"SELECT collection, COUNT(*) AS n FROM knowledge_documents WHERE {clause} "
                "GROUP BY collection ORDER BY collection",
                *params,
            )
            return [(r["collection"], int(r["n"])) for r in rows]

        return await self._run(op)

    async def erase_under(self, prefix: str) -> int:
        clause, params = _under("collection", prefix.strip("/"))

        async def op(tx: Tx) -> int:
            for table in ("knowledge_topics", "knowledge_sections"):
                await tx.execute(f"DELETE FROM {table} WHERE {clause}", *params)
            return await tx.execute(f"DELETE FROM knowledge_documents WHERE {clause}", *params)

        return await self._run(op)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        root = f"tenants/{tenant_id}" + (f"/users/{user_id}" if user_id else "")
        return await self.erase_under(root)

    # ── enrichment ──────────────────────────────────────────────────────────
    async def brief(self, *, collection: str, document: str) -> DocumentBrief | None:
        collection = collection.strip("/")
        info = await self.info(collection, document)
        if info is None:
            return None

        async def op(tx: Tx) -> DocumentBrief:
            rows = await tx.fetchall(
                "SELECT * FROM knowledge_sections WHERE collection = ? AND document = ? ORDER BY position",
                collection, document,
            )  # fmt: skip
            return DocumentBrief(
                document, info.title, info.filename, info.pages,
                tuple(
                    SectionBrief(
                        int(r["position"]), r["title"], tuple(json.loads(r["heading_path"])),
                        int(r["first_page"]), int(r["last_page"]), int(r["tokens"]), r["text"],
                    )
                    for r in rows
                ),
            )  # fmt: skip

        return await self._run(op)

    async def topic_tree(self, *, collection: str) -> list[str]:
        async def op(tx: Tx) -> list[str]:
            rows = await tx.fetchall(
                "SELECT DISTINCT topic FROM knowledge_topics WHERE collection = ? ORDER BY topic LIMIT 200",
                collection.strip("/"),
            )
            return [r["topic"] for r in rows]

        return await self._run(op)

    async def claim(self, *, collection: str, document: str, enricher: str) -> bool:
        collection = collection.strip("/")
        now = time.time()

        async def op(tx: Tx) -> bool:
            changed = await tx.execute(
                "UPDATE knowledge_documents SET enrichment = 'running', enricher = ?, attempts = attempts + 1, enriched_at = ? "
                "WHERE collection = ? AND document = ? AND attempts < ? AND "
                "((enrichment IN ('none', 'failed')) OR (enrichment = 'running' AND enriched_at < ?) "
                "OR (enrichment = 'done' AND enricher <> ?))",
                enricher, now, collection, document, MAX_ATTEMPTS, now - STUCK_AFTER_S, enricher,
            )  # fmt: skip
            return changed > 0

        return await self._run(op)

    async def annotate(
        self, *, collection: str, document: str, described: Described, enricher: str
    ) -> None:
        collection = collection.strip("/")

        async def op(tx: Tx) -> None:
            await tx.execute(
                "UPDATE knowledge_documents SET description = ?, enrichment = 'done', enricher = ?, attempts = 0, "
                "enriched_at = ? WHERE collection = ? AND document = ?",
                described.card, enricher, time.time(), collection, document,
            )  # fmt: skip
            for position, text in described.sections.items():
                await tx.execute(
                    "UPDATE knowledge_sections SET description = ? WHERE collection = ? AND document = ? AND position = ?",
                    text, collection, document, int(position),
                )  # fmt: skip
            await tx.execute(
                "DELETE FROM knowledge_topics WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            for topic in described.topics:
                await tx.execute(
                    "INSERT INTO knowledge_topics (collection, document, topic) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    collection, document, topic,
                )  # fmt: skip

        await self._run(op)

    async def fail(self, *, collection: str, document: str, error: str) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute(
                "UPDATE knowledge_documents SET enrichment = 'failed', enriched_at = ? WHERE collection = ? AND document = ?",
                time.time(), collection.strip("/"), document,
            )  # fmt: skip

        await self._run(op)

    async def pending(
        self, tenant_id: str, *, enricher: str, limit: int = 50
    ) -> list[tuple[str, str]]:
        async def op(tx: Tx) -> list[tuple[str, str]]:
            rows = await tx.fetchall(
                "SELECT collection, document FROM knowledge_documents WHERE tenant_id = ? AND attempts < ? AND "
                "(enrichment IN ('none', 'failed') OR (enrichment = 'running' AND enriched_at < ?) "
                "OR (enrichment = 'done' AND enricher <> ?)) ORDER BY seq LIMIT ?",
                tenant_id, MAX_ATTEMPTS, time.time() - STUCK_AFTER_S, enricher, limit,
            )  # fmt: skip
            return [(r["collection"], r["document"]) for r in rows]

        return await self._run(op)


def _under(column: str, prefix: str) -> tuple[str, list[Any]]:
    return under(column, prefix)


__all__ = ["COMPONENT", "SCRIPTS", "LocalLibrary", "document_id", "tenant_of"]
