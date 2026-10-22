"""``Library`` — documents a model can navigate, kept as an Open Knowledge Format bundle with a catalog beside it.

::

    library = Library(database, files)                       # reads with Reader()
    added = await library.add(data, "q3.pdf", collection="tenants/acme/conversations/c1/documents")
    await library.list(collection=...); await library.outline(...); await library.read(...); await library.find(...)

**The bundle is the source of truth** — plain markdown files with YAML frontmatter (OKF v0.2) in a ``FileStore``::

    {collection}/index.md                        what is here
    {collection}/{doc}/index.md                  type: Index  — the document's outline
    {collection}/{doc}/NN-{slug}.md              type: Section — one concept, with Prev/Up/Next links
    {collection}/{doc}/images/{id}.png           figures, charts, table crops

Anything that reads markdown — a person, ``grep``, another agent — can read it. **The catalog is derived**: three tables
(``library_documents``, ``library_sections`` with a full-text index, ``library_images``) rebuilt from the bundle by ``reindex``. There are no
embeddings: the model navigates — ``list``, ``outline``, ``read``, ``find`` — the way a person opens a folder, then a table of contents,
then a chapter.

Every method takes the ``collection`` (a key prefix) it works in. A collection is whatever the caller says it is — a conversation's
documents, a company's handbook — and whoever exposes it (an agent tool, a server route) decides it, never the model.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from loamz.engine.documents import okf
from loamz.engine.documents.bundle import (
    Bundle,
    BundleImage,
    build_bundle,
    document_id,
    headings_in,
    pages_label,
    slug,
    strip_navigation,
)
from loamz.engine.documents.chunking import chunk_section, chunk_size_for
from loamz.engine.documents.enrichment import (
    MAX_TOPIC_CHILDREN,
    STALE_RUNNING_S,
    DocumentBrief,
    Enriched,
    Enricher,
    EnrichmentUsage,
    Organiser,
    Reorganised,
    SectionBrief,
    clean,
    sentences,
    topic_path,
)
from loamz.engine.documents.enrichment import (
    enrich as enrich_document,
)
from loamz.engine.documents.enrichment import (
    reorganise as reorganise_collection,
)
from loamz.engine.documents.reader import Reader
from loamz.engine.documents.split import Section, tokens
from loamz.engine.documents.types import (
    Added,
    DocumentError,
    DocumentInfo,
    EnrichmentState,
    ExtractionResult,
    Hit,
    Hits,
    Listing,
    Outline,
    Passage,
    Picture,
    SectionInfo,
    Topic,
)
from loamz.engine.errors import ContextLengthError, ServiceUnavailableError
from loamz.engine.host import Storage
from loamz.engine.models.protocols import EmbeddingModel, Modality, Reranker
from loamz.engine.models.remote import RemoteEmbedder, RemoteReranker
from loamz.engine.sql import Database, Tx, textsearch
from loamz.engine.sql import under as db_under
from loamz.engine.types import MediaBlock, TextBlock
from loamz.engine.vectors.contract import Document

if TYPE_CHECKING:
    from loamz.engine.files.contract import FileStore

logger = logging.getLogger(__name__)

_COMPONENT = "library"
_PAGE = re.compile(r"<!-- page (\d+) -->")
MAX_READ_CHARS = 24_000
MAX_OUTLINE = 60
MAX_LIST = 50
MAX_IMAGE_BYTES = 4 * 1024 * 1024
INDEX_LISTING = 100
EMBED_BATCH = 64
RERANK_CANDIDATES = 20


def _schema(database: Database) -> str:
    return (
        """
CREATE TABLE IF NOT EXISTS library_documents (
    seq {pk},
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    resource TEXT,
    engine TEXT NOT NULL,
    pages INTEGER NOT NULL,
    sections INTEGER NOT NULL,
    images INTEGER NOT NULL,
    needs_ocr TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    added_at DOUBLE PRECISION NOT NULL,
    UNIQUE (collection, document)
);
CREATE TABLE IF NOT EXISTS library_sections (
    seq {pk},
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    first_page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    tokens INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (collection, document, position)
);
"""
        + textsearch.ddl(database.dialect, index="library_sections_fts", table="library_sections")
        + """
CREATE TABLE IF NOT EXISTS library_images (
    seq {pk},
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    image TEXT NOT NULL,
    page INTEGER NOT NULL,
    label TEXT NOT NULL,
    caption TEXT,
    media_type TEXT NOT NULL,
    UNIQUE (collection, document, image)
);
"""
    )


def _enrichment_schema(database: Database) -> str:
    """Migration 2: what a model wrote about the documents, kept apart from the facts so that the catalog stays a pure function of the bundle's
    text and the descriptions can be rebuilt, versioned and dropped on their own."""
    return (
        """
CREATE TABLE IF NOT EXISTS library_enrichment (
    seq {pk},
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    position INTEGER NOT NULL,
    description TEXT NOT NULL,
    UNIQUE (collection, document, position)
);
"""
        + textsearch.ddl(
            database.dialect,
            index="library_enrichment_fts",
            table="library_enrichment",
            column="description",
        )
        + """
CREATE TABLE IF NOT EXISTS library_enrichment_state (
    seq {pk},
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    state TEXT NOT NULL,
    enricher TEXT NOT NULL,
    version TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    error TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    UNIQUE (collection, document)
);
CREATE TABLE IF NOT EXISTS library_topics (
    seq {pk},
    collection TEXT NOT NULL,
    document TEXT NOT NULL,
    topic TEXT NOT NULL,
    UNIQUE (collection, document, topic)
);
"""
    )


SCHEMA = [_schema, _enrichment_schema]
_ENRICHMENT_TABLES = (
    "library_enrichment",
    "library_enrichment_state",
    "library_topics",
)
TOPICS_DIR = "_topics"
TOPIC_TREE_LIMIT = 200


class Library:
    def __init__(
        self,
        database: Database,
        files: FileStore,
        *,
        reader: Reader | None = None,
        embedder: EmbeddingModel | str | None = None,
        reranker: Reranker | str | None = None,
        chunk_tokens: int | None = None,
        enricher: Enricher | None = None,
        organiser: Organiser | None = None,
        file_topics: bool = False,
        enrich_timeout: float = 180.0,
    ) -> None:
        """``database`` keeps the catalog (and the chunks' vectors); ``files`` the bundle; ``reader`` reads what is added.

        With no ``embedder`` the library is lexical: ``find`` matches words in the catalog's full-text index, which is all a conversation's
        documents need. With an ``embedder`` — an ``EmbeddingModel``, or the URL of an embedding service (``RemoteEmbedder``) — each section is
        also cut into chunks and embedded (and figures too, if the model takes images), and ``find`` fuses similarity with words, then reranks
        with ``reranker`` (a ``Reranker`` or a URL) if there is one. A service that is down degrades ``find`` to words and stores chunks
        without vectors for ``reindex(missing_only=True)`` to embed later; it never fails an ``add``.

        An ``enricher`` (see ``loamz.engine.documents.enrichment``) lets ``enrich`` write a card per document and a description per section, which
        ``list``, ``outline`` and ``find`` then return; ``file_topics`` also files each document in a topic tree (``browse``). ``add`` itself never
        calls it: run ``enrich`` after, in the background, so an upload never waits for a model. An ``organiser`` lets ``reorganise`` redraw the whole
        topic tree at once, which ``needs_reorganising`` says is due."""
        self._store = Storage(database)
        self._files = files
        self._reader = reader or Reader()
        self._embedder: EmbeddingModel | None = (
            RemoteEmbedder(embedder) if isinstance(embedder, str) else embedder
        )
        self._reranker: Reranker | None = (
            RemoteReranker(reranker) if isinstance(reranker, str) else reranker
        )
        self._chunk_tokens = chunk_tokens
        self._enricher = enricher
        self._organiser = organiser
        self._file_topics = file_topics
        self._enrich_timeout = enrich_timeout

    async def _run(self, fn):
        await self._store.ensure(_COMPONENT, SCHEMA)
        return await self._store.run(fn)

    @staticmethod
    def document_id(filename: str, sha256: str) -> str:
        """The id ``add`` gives a file: its name's slug and the first six characters of the SHA-256 of its bytes — so a caller that
        already has the checksum can find the document without asking."""
        return document_id(filename, sha256)

    @property
    def file_topics(self) -> bool:
        """Whether documents are filed in a topic tree (``browse``)."""
        return self._file_topics

    # -------------------------------------------------------------------------------------------------------------- add

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
        """Read ``source`` (bytes, or an ``ExtractionResult`` you already have) and file it in ``collection``.

        Adding the same file again is a no-op (``duplicate=True``). ``resource`` says where the original lives (an object key, a URL);
        ``metadata`` is kept with the document and returned with it (a file id, say). When you pass an ``ExtractionResult``, pass the SHA-256 of
        the original bytes as ``sha256`` if you have it: the document's id (``document_id``) is derived from it. Raises ``DocumentError`` for a file that could
        not be read or has no readable text.

        It is ``prepare`` (read and cut; no storage), then the chunks' vectors, then ``file`` (the bundle) and the catalog (what ``index``
        rebuilds from the files): a collection written by another embedder is refused before anything is written."""
        bundle = await self.prepare(
            source,
            filename,
            resource=resource,
            content_type=content_type,
            metadata=metadata,
            sha256=sha256,
        )
        collection = collection.strip("/")
        if await self._exists(collection, bundle.document):
            info = await self.info(collection, bundle.document)
            assert info is not None
            return Added(
                bundle.document,
                info.title,
                info.filename,
                info.pages,
                info.sections,
                info.images,
                info.needs_ocr,
                duplicate=True,
            )
        unembedded = await self._vectors_of(collection, bundle)
        await self.file(bundle, collection=collection)
        await self._catalog_of(collection, bundle)
        await self._write_collection_index(collection)
        warnings = list(bundle.warnings)
        if unembedded:
            warnings.append(
                f"{unembedded} passage{'s' if unembedded != 1 else ''} could not be embedded (the embedding service was unavailable) and "
                "will be found by their words only until reindex(missing_only=True) embeds them"
            )
        return Added(
            bundle.document,
            bundle.title,
            bundle.filename,
            bundle.pages,
            len(bundle.sections),
            len(bundle.images),
            bundle.needs_ocr,
            warnings=tuple(warnings),
        )

    async def prepare(
        self,
        source: bytes | ExtractionResult,
        filename: str = "",
        *,
        resource: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        sha256: str | None = None,
    ) -> Bundle:
        """Read ``source`` and make it ready to file: its sections, figures and the OKF files of the bundle. Touches no storage (the reader
        is the only thing it calls). Raises ``DocumentError`` for a file that could not be read or has no readable text."""
        if isinstance(source, ExtractionResult):
            result, digest = (
                source,
                sha256 or hashlib.sha256(source.markdown.encode("utf-8")).hexdigest(),
            )
        else:
            digest = hashlib.sha256(source).hexdigest()
            result = await self._reader.read(source, filename, content_type=content_type)
        return build_bundle(
            result,
            filename=filename,
            sha256=digest,
            resource=resource,
            metadata=metadata,
        )

    async def file(self, bundle: Bundle, *, collection: str) -> None:
        """Write ``bundle``'s files (figures, sections, then the document's index) into ``collection``'s folder of the file store. The
        catalog is not touched: ``index`` builds it."""
        base = f"{collection.strip('/')}/{bundle.document}"
        for member in bundle.files:
            await self._files.upload(
                f"{base}/{member.path}", member.data, content_type=member.content_type
            )

    async def index(self, collection: str, document: str) -> int:
        """Build the catalog of a document already filed in ``collection`` — and, with an embedder, its chunks and vectors — from the files of
        its bundle: the code ``reindex`` runs for each document. Returns how many chunks were stored without a vector. Raises
        ``DocumentError`` if there is no such bundle."""
        collection = collection.strip("/")
        names = [
            key[len(f"{collection}/{document}/") :]
            for key, _size, _mtime in await self._files.list_prefix(f"{collection}/{document}/")
        ]
        if "index.md" not in names:
            raise DocumentError(f"{collection}/{document} has no bundle to index")
        return await self._index_filed(collection, document, names)

    async def _index_filed(self, collection: str, document: str, names: list[str]) -> int:
        index = okf.parse(
            (await self._files.download(f"{collection}/{document}/index.md")).decode(
                "utf-8", "replace"
            )
        )
        sections: list[Section] = []
        written: dict[int, okf.Concept] = {}
        for name in sorted(
            n for n in names if n != "index.md" and "/" not in n and n.endswith(".md")
        ):
            concept = okf.parse(
                (await self._files.download(f"{collection}/{document}/{name}")).decode(
                    "utf-8", "replace"
                )
            )
            pages = concept.extra.get("pages") or [1, 1]
            written[len(sections) + 1] = concept
            sections.append(
                Section(
                    concept.title or name,
                    tuple(concept.extra.get("heading_path") or ()),
                    int(pages[0]),
                    int(pages[-1]),
                    strip_navigation(concept.body),
                )
            )
        images = tuple(
            BundleImage(
                n.rsplit("/", 1)[-1].removesuffix(".png"),
                _page_of(n),
                "figure",
                None,
                "image/png",
            )
            for n in names
            if n.startswith("images/")
        )
        extra = index.extra
        bundle = Bundle(
            document=document,
            title=index.title or document,
            filename=str(extra.get("filename", "")),
            sha256=str(extra.get("sha256", "")),
            resource=index.resource,
            engine=str(extra.get("engine", "")),
            pages=int(extra.get("pages", 1)),
            sections=tuple(sections),
            images=images,
            needs_ocr=tuple(int(p) for p in extra.get("needs_ocr", [])),
            metadata=dict(extra.get("metadata") or {}),
        )
        await self._catalog_of(collection, bundle)
        await self._restore_enrichment(collection, document, index, written)
        if self._embedder is None:
            return 0
        await self._store.vectors.delete_where(collection=collection, filter={"document": document})
        return await self._vectors_of(collection, bundle)

    async def _catalog_of(self, collection: str, bundle: Bundle) -> None:
        info = DocumentInfo(
            bundle.document,
            bundle.title,
            bundle.filename,
            bundle.pages,
            len(bundle.sections),
            len(bundle.images),
            bundle.needs_ocr,
            bundle.engine,
            bundle.resource,
            bundle.metadata,
        )
        await self._catalog(
            collection,
            info,
            bundle.sha256,
            bundle.sections,
            [(i.id, i.page, i.label, i.caption, i.media_type) for i in bundle.images],
        )

    async def _vectors_of(self, collection: str, bundle: Bundle) -> int:
        return await self._index_vectors(
            collection,
            bundle.document,
            bundle.title,
            bundle.filename,
            bundle.sections,
            bundle.images,
            bundle.metadata,
        )

    async def _exists(self, collection: str, document: str) -> bool:
        async def op(tx: Tx) -> bool:
            row = await tx.fetchone(
                "SELECT 1 AS found FROM library_documents WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            return row is not None

        return await self._run(op)

    async def _catalog(
        self,
        collection: str,
        info: DocumentInfo,
        digest: str,
        sections: Sequence[Section],
        images: Sequence[tuple[str, int, str, str | None, str]],
    ) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM library_sections WHERE collection = ? AND document = ?",
                collection,
                info.document,
            )
            await tx.execute(
                "DELETE FROM library_images WHERE collection = ? AND document = ?",
                collection,
                info.document,
            )
            await tx.execute(
                "DELETE FROM library_documents WHERE collection = ? AND document = ?",
                collection,
                info.document,
            )
            await tx.execute(
                "INSERT INTO library_documents (collection, document, title, filename, resource, engine, pages, sections, images, needs_ocr, sha256, meta_json, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                collection, info.document, info.title, info.filename, info.resource, info.engine, info.pages, info.sections, info.images,
                json.dumps(list(info.needs_ocr)), digest, json.dumps(info.meta), time.time(),
            )  # fmt: skip
            for n, section in enumerate(sections, start=1):
                await tx.execute(
                    "INSERT INTO library_sections (collection, document, position, title, heading_path, first_page, last_page, tokens, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    collection, info.document, n, section.title, json.dumps(list(section.heading_path)), section.first_page, section.last_page, section.tokens, section.markdown,
                )  # fmt: skip
            for image, page, label, caption, media_type in images:
                await tx.execute(
                    "INSERT INTO library_images (collection, document, image, page, label, caption, media_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    collection, info.document, image, page, label, caption, media_type,
                )  # fmt: skip

        await self._run(op)

    async def _write_collection_index(self, collection: str) -> None:
        listing = await self.list(collection=collection, limit=INDEX_LISTING)
        total = await self._count(collection)
        lines = [f"- [{d.title}]({d.document}/index.md) — {_blurb(d)}" for d in listing.documents]
        if self._file_topics and any(d.topics for d in listing.documents):
            lines.insert(0, f"Browse by topic: [{TOPICS_DIR}/index.md]({TOPICS_DIR}/index.md)\n")
        if total > len(listing.documents):
            lines.append(f"- … and {total - len(listing.documents)} more")
        concept = okf.Concept(
            type="Index",
            title="Documents",
            description=f"{total} document{'s' if total != 1 else ''}",
            body="\n".join(lines),
        )
        await self._files.upload(
            f"{collection}/index.md",
            okf.serialize(concept).encode("utf-8"),
            content_type="text/markdown",
        )

    async def _count(self, collection: str) -> int:
        async def op(tx: Tx) -> int:
            row = await tx.fetchone(
                "SELECT COUNT(*) AS n FROM library_documents WHERE collection = ?",
                collection,
            )
            return int(row["n"]) if row else 0

        return await self._run(op)

    # ------------------------------------------------------------------------------------------------------- navigating

    @staticmethod
    def _doc(row: Any) -> DocumentInfo:
        return DocumentInfo(
            document=row["document"],
            title=row["title"],
            filename=row["filename"],
            pages=int(row["pages"]),
            sections=int(row["sections"]),
            images=int(row["images"]),
            needs_ocr=tuple(json.loads(row["needs_ocr"])),
            engine=row["engine"],
            resource=row["resource"],
            meta=json.loads(row["meta_json"]),
        )

    @staticmethod
    def _section(row: Any) -> SectionInfo:
        return SectionInfo(
            position=int(row["position"]),
            title=row["title"],
            heading_path=tuple(json.loads(row["heading_path"])),
            first_page=int(row["first_page"]),
            last_page=int(row["last_page"]),
            tokens=int(row["tokens"]),
        )

    async def info(self, collection: str, document: str) -> DocumentInfo | None:
        collection = collection.strip("/")

        async def op(tx: Tx) -> DocumentInfo | None:
            row = await tx.fetchone(
                "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            if row is None:
                return None
            return (await self._decorate(tx, collection, [self._doc(row)]))[0]

        return await self._run(op)

    async def _decorate(
        self, tx: Tx, collection: str, docs: list[DocumentInfo]
    ) -> list[DocumentInfo]:
        """``docs`` with what a model wrote about them (card, topics, state), when it has."""
        if not docs:
            return docs
        marks = ",".join("?" * len(docs))
        ids = [d.document for d in docs]
        cards = {
            r["document"]: r["description"]
            for r in await tx.fetchall(
                f"SELECT document, description FROM library_enrichment WHERE collection = ? AND position = 0 AND document IN ({marks})",  # noqa: S608
                collection, *ids,
            )
        }  # fmt: skip
        states = {
            r["document"]: r["state"]
            for r in await tx.fetchall(
                f"SELECT document, state FROM library_enrichment_state WHERE collection = ? AND document IN ({marks})",  # noqa: S608
                collection, *ids,
            )
        }  # fmt: skip
        topics: dict[str, list[str]] = defaultdict(list)
        for r in await tx.fetchall(
            f"SELECT document, topic FROM library_topics WHERE collection = ? AND document IN ({marks}) ORDER BY seq",  # noqa: S608
            collection, *ids,
        ):  # fmt: skip
            topics[r["document"]].append(r["topic"])
        return [
            replace(
                d,
                description=cards.get(d.document),
                topics=tuple(topics.get(d.document, ())),
                enrichment=states.get(d.document, "none"),
            )
            for d in docs
        ]

    async def _section_descriptions(self, tx: Tx, collection: str, document: str) -> dict[int, str]:
        rows = await tx.fetchall(
            "SELECT position, description FROM library_enrichment WHERE collection = ? AND document = ? AND position > 0",
            collection, document,
        )  # fmt: skip
        return {int(r["position"]): r["description"] for r in rows}

    async def list(
        self, *, collection: str, limit: int = MAX_LIST, after: str | None = None
    ) -> Listing:
        """The documents of ``collection``, oldest first, ``limit`` (at most ``MAX_LIST``) at a time."""
        collection, limit = collection.strip("/"), max(1, min(limit, MAX_LIST))
        cursor = int(after) if after and after.isdigit() else 0

        async def op(tx: Tx) -> tuple[list[DocumentInfo], int | None]:
            rows = await tx.fetchall(
                "SELECT * FROM library_documents WHERE collection = ? AND seq > ? ORDER BY seq LIMIT ?",
                collection,
                cursor,
                limit + 1,
            )
            page = rows[:limit]
            docs = await self._decorate(tx, collection, [self._doc(r) for r in page])
            return docs, (int(page[-1]["seq"]) if len(rows) > limit else None)

        documents, last = await self._run(op)
        return Listing(tuple(documents), str(last) if last is not None else None)

    async def collections(self, *, under: str) -> list[tuple[str, int]]:
        """``(collection, document count)`` for every collection at or under the prefix ``under``, by name."""
        under = under.strip("/")

        clause, params = db_under("collection", under)

        async def op(tx: Tx) -> list[tuple[str, int]]:
            rows = await tx.fetchall(
                f"SELECT collection, COUNT(*) AS n FROM library_documents WHERE {clause} GROUP BY collection ORDER BY collection",  # noqa: S608
                *params,
            )
            return [(row["collection"], int(row["n"])) for row in rows]

        return await self._run(op)

    async def outline(
        self, *, collection: str, document: str, section: int | None = None
    ) -> Outline | None:
        """A document's sections — or, with ``section``, the headings inside that one. ``None`` if there is no such document or section."""
        collection = collection.strip("/")

        async def op(tx: Tx) -> Outline | None:
            doc_row = await tx.fetchone(
                "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            if doc_row is None:
                return None
            info = (await self._decorate(tx, collection, [self._doc(doc_row)]))[0]
            described = await self._section_descriptions(tx, collection, document)
            if section is not None:
                row = await tx.fetchone(
                    "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND position = ?",
                    collection,
                    document,
                    section,
                )
                if row is None:
                    return None
                headings = headings_in(row["text"])
                return Outline(
                    info,
                    (replace(self._section(row), description=described.get(section)),),
                    more=max(0, len(headings) - MAX_OUTLINE),
                    headings=tuple(headings[:MAX_OUTLINE]),
                )
            rows = await tx.fetchall(
                "SELECT position, title, heading_path, first_page, last_page, tokens, text FROM library_sections "
                "WHERE collection = ? AND document = ? ORDER BY position LIMIT ?",
                collection, document, MAX_OUTLINE,
            )  # fmt: skip
            sections = []
            for row in rows:
                inside = tuple(h[1] for h in headings_in(row["text"]) if h[1] != row["title"])[:6]
                base = self._section(row)
                sections.append(
                    replace(
                        base,
                        headings=inside,
                        description=described.get(base.position),
                    )
                )
            return Outline(info, tuple(sections), more=max(0, info.sections - len(sections)))

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
        """A section's text, or the text of ``pages`` (first, last), at most ``MAX_READ_CHARS`` from ``offset``; ``next_offset`` says
        where to continue. ``None`` if there is no such document, section, or text on those pages."""
        collection = collection.strip("/")

        async def op(tx: Tx) -> Passage | None:
            doc_row = await tx.fetchone(
                "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                collection,
                document,
            )
            if doc_row is None:
                return None
            if pages is not None:
                first, last = pages
                rows = await tx.fetchall(
                    "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND last_page >= ? AND first_page <= ? ORDER BY position",
                    collection, document, first, last,
                )  # fmt: skip
                text = _slice_pages("\n\n".join(r["text"] for r in rows), first, last)
            else:
                row = await tx.fetchone(
                    "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND position = ?",
                    collection,
                    document,
                    section or 1,
                )
                rows = [row] if row else []
                text = row["text"] if row else ""
            if not rows or not text.strip():
                return None
            offset_ = max(0, min(offset, len(text)))
            chunk = text[offset_ : offset_ + MAX_READ_CHARS]
            end = offset_ + len(chunk)
            return Passage(
                self._doc(doc_row),
                self._section(rows[0]),
                chunk,
                offset_,
                end if end < len(text) else None,
                len(text),
            )

        return await self._run(op)

    async def find(
        self,
        *,
        collection: str,
        query: str,
        document: str | None = None,
        limit: int = 8,
    ) -> Hits:
        """The sections that answer ``query``, best first.

        By words: every word first, relaxing to any word when no section has them all. With an embedder, similarity and words are fused over the
        collection's chunks, the best candidates are reranked if there is a reranker, and chunks are folded into their sections (a matching
        figure is a hit of its own, with its ``image``). If the embedding or rerank service is down the answer is words only, and
        ``note`` says so."""
        collection, limit = collection.strip("/"), max(1, min(limit, 20))
        note: str | None = None
        if self._embedder is not None and textsearch.terms(query):
            try:
                hits = await self._find_semantic(collection, query, document, limit)
                if hits:
                    return hits
            except ServiceUnavailableError as exc:
                logger.warning("semantic search unavailable, matching words only: %s", exc)
                note = "The embedding service is unavailable, so these were matched by their words only; a relevant passage may be missing."
        hits = Hits(await self._find_lexical(collection, query, document, limit))
        hits.note = note
        return hits

    async def _find_lexical(
        self, collection: str, query: str, document: str | None, limit: int
    ) -> list[Hit]:
        query_words = textsearch.terms(query)
        if not query_words:
            return []
        dialect = tx_dialect(self)

        async def op(tx: Tx) -> list[Hit]:
            for mode in ("all", "any"):
                # a section matches by its own words, by what a model wrote about it, or both (then the scores add)
                found: dict[tuple[str, int], list[Any]] = {}
                source, where, params = textsearch.ranked(
                    dialect,
                    index="library_sections_fts",
                    table="library_sections",
                    alias="s",
                    query_words=query_words,
                    match=mode,
                )
                score = textsearch.score(dialect, index="library_sections_fts", alias="s")
                sql = (
                    f"SELECT s.*, {score} AS score FROM {source} WHERE {where} AND s.collection = ?"
                )
                args: list[Any] = [*params, collection]
                if document:
                    sql += " AND s.document = ?"
                    args.append(document)
                for row in await tx.fetchall(sql + " ORDER BY score DESC LIMIT ?", *args, limit):
                    found[(row["document"], int(row["position"]))] = [
                        row,
                        float(row["score"]),
                        False,
                    ]

                source, where, params = textsearch.ranked(
                    dialect,
                    index="library_enrichment_fts",
                    table="library_enrichment",
                    alias="e",
                    query_words=query_words,
                    match=mode,
                )
                score = textsearch.score(dialect, index="library_enrichment_fts", alias="e")
                sql = f"SELECT e.*, {score} AS score FROM {source} WHERE {where} AND e.collection = ? AND e.position > 0"
                args = [*params, collection]
                if document:
                    sql += " AND e.document = ?"
                    args.append(document)
                for erow in await tx.fetchall(sql + " ORDER BY score DESC LIMIT ?", *args, limit):
                    key = (erow["document"], int(erow["position"]))
                    if key in found:
                        found[key][1] += float(erow["score"])
                        continue
                    srow = await tx.fetchone(
                        "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND position = ?",
                        collection, erow["document"], erow["position"],
                    )  # fmt: skip
                    if srow is not None:
                        found[key] = [srow, float(erow["score"]), True]
                if not found:
                    continue
                hits: list[Hit] = []
                for srow, sc, by_description in (
                    v for _k, v in sorted(found.items(), key=lambda kv: -kv[1][1])[:limit]
                ):
                    doc_row = await tx.fetchone(
                        "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                        collection, srow["document"],
                    )  # fmt: skip
                    if doc_row is None:
                        continue
                    info, section = await self._hit_parts(tx, collection, doc_row, srow)
                    snippet = (
                        section.description
                        if by_description and section.description
                        else textsearch.snippet(srow["text"], query_words)
                    )
                    hits.append(Hit(info, section, snippet or "", sc))
                return hits
            return []

        return await self._run(op)

    async def _hit_parts(
        self, tx: Tx, collection: str, doc_row: Any, section_row: Any
    ) -> tuple[DocumentInfo, SectionInfo]:
        """The document and section of a search hit, with what a model wrote about each."""
        info = (await self._decorate(tx, collection, [self._doc(doc_row)]))[0]
        described = await self._section_descriptions(tx, collection, doc_row["document"])
        section = self._section(section_row)
        return info, replace(section, description=described.get(section.position))

    async def _find_semantic(
        self, collection: str, query: str, document: str | None, limit: int
    ) -> Hits:
        assert self._embedder is not None
        vectors = self._store.vectors
        embedded = await self._embedder.embed([query], query=True)
        found = await vectors.hybrid_search(
            embedded.embeddings[0],
            query,
            collection=collection,
            fused_k=RERANK_CANDIDATES,
            filter={"document": document} if document else None,
            space=embedded.model or self._embedder.model or None,
        )
        note: str | None = None
        scores = [r.score for r in found]
        if self._reranker is not None and len(found) > 1:
            try:
                scores = await self._reranker.rerank(query, [r.to_text() for r in found])
            except ServiceUnavailableError as exc:
                logger.warning("rerank unavailable, keeping the fused order: %s", exc)
                note = "The reranking service is unavailable, so these are in the order the search found them."
        ranked = sorted(zip(scores, found, strict=True), key=lambda pair: -pair[0])
        query_words = textsearch.terms(query)
        seen: set[tuple[str, int, str]] = set()
        picked: list[tuple[float, Any, str | None]] = []
        for score, result in ranked:
            meta = result.metadata
            key = (
                str(meta.get("document", "")),
                int(meta.get("section", 0) or 0),
                str(meta.get("image", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            picked.append((score, result, meta.get("image")))
            if len(picked) == limit:
                break

        async def op(tx: Tx) -> list[Hit]:
            hits: list[Hit] = []
            for score, result, image in picked:
                meta = result.metadata
                doc_row = await tx.fetchone(
                    "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                    collection,
                    meta.get("document"),
                )
                if doc_row is None:
                    continue  # a chunk whose document was deleted from the catalog: not a hit
                if image:
                    page = int(meta.get("pages", [1])[0])
                    row = await tx.fetchone(
                        "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND first_page <= ? AND last_page >= ? ORDER BY position LIMIT 1",
                        collection, meta.get("document"), page, page,
                    )  # fmt: skip
                else:
                    row = await tx.fetchone(
                        "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND position = ?",
                        collection, meta.get("document"), int(meta.get("section", 1)),
                    )  # fmt: skip
                if row is not None:
                    info, section = await self._hit_parts(tx, collection, doc_row, row)
                    hits.append(
                        Hit(
                            info,
                            section,
                            textsearch.snippet(result.to_text(), query_words),
                            float(score),
                            image=image,
                        )
                    )
            return hits

        hits = Hits(await self._run(op))
        hits.note = note
        return hits

    async def view(
        self,
        *,
        collection: str,
        image: str | None = None,
        document: str | None = None,
        page: int | None = None,
    ) -> Picture | None:
        """One picture: by its id, or the first one on ``page`` of ``document``. ``None`` if there is none (or it is too large to show)."""
        collection = collection.strip("/")

        async def op(tx: Tx) -> Any:
            if image:
                sql, args = (
                    "SELECT * FROM library_images WHERE collection = ? AND image = ?",
                    [collection, image],
                )
                if document:
                    sql += " AND document = ?"
                    args.append(document)
                return await tx.fetchone(sql + " ORDER BY seq LIMIT 1", *args)
            if document and page is not None:
                return await tx.fetchone(
                    "SELECT * FROM library_images WHERE collection = ? AND document = ? AND page = ? ORDER BY seq LIMIT 1",
                    collection,
                    document,
                    page,
                )
            return None

        row = await self._run(op)
        if row is None:
            return None
        key = f"{collection}/{row['document']}/images/{row['image']}.png"
        try:
            data = await self._files.download(key)
        except Exception:  # noqa: BLE001 — a catalog row whose file is gone is "no such picture"
            return None
        if len(data) > MAX_IMAGE_BYTES:
            return None
        return Picture(
            data,
            row["media_type"],
            row["document"],
            row["image"],
            int(row["page"]),
            row["label"],
            row["caption"],
        )

    # -------------------------------------------------------------------------------------------------------- removing

    async def delete(self, *, collection: str, document: str) -> bool:
        """Remove one document — bundle and catalog. ``False`` if it was not there."""
        collection = collection.strip("/")
        existed = await self._exists(collection, document)
        before = await self._topics_of(collection, document)
        await self._files.delete_prefix(f"{collection}/{document}/")
        await self._store.vectors.delete_where(collection=collection, filter={"document": document})

        async def op(tx: Tx) -> None:
            for table in (
                "library_sections",
                "library_images",
                "library_documents",
                *_ENRICHMENT_TABLES,
            ):
                await tx.execute(
                    f"DELETE FROM {table} WHERE collection = ? AND document = ?",
                    collection,
                    document,
                )  # noqa: S608 — fixed names

        await self._run(op)
        if existed:
            await self._write_topic_pages(collection, before)
            await self._write_collection_index(collection)
        return existed

    async def erase_under(self, prefix: str) -> int:
        """Remove every document in every collection at or under ``prefix`` (a tenant leaving, a user erased): the catalog rows, each
        document's bundle, and each collection's index. Only what this library wrote — other files under ``prefix`` are not touched.
        Returns the number of documents removed."""
        prefix = prefix.strip("/")
        clause, params = db_under("collection", prefix)

        async def op(tx: Tx) -> list[tuple[str, str]]:
            rows = await tx.fetchall(
                f"SELECT collection, document FROM library_documents WHERE {clause}",  # noqa: S608
                *params,
            )
            for table in (
                "library_sections",
                "library_images",
                "library_documents",
                *_ENRICHMENT_TABLES,
            ):
                await tx.execute(f"DELETE FROM {table} WHERE {clause}", *params)  # noqa: S608 — fixed names
            return [(r["collection"], r["document"]) for r in rows]

        removed = await self._run(op)
        await self._store.vectors.erase_under(prefix)
        for collection, document in removed:
            await self._files.delete_prefix(f"{collection}/{document}/")
        for collection in {c for c, _d in removed}:
            await self._files.delete(f"{collection}/index.md")
            await self._files.delete_prefix(f"{collection}/{TOPICS_DIR}/")
        return len(removed)

    # ------------------------------------------------------------------------------------------------------- enrichment

    async def enrich(
        self,
        *,
        collection: str,
        document: str,
        enricher: Enricher | None = None,
        force: bool = False,
    ) -> Enriched:
        """Have ``enricher`` (default: the library's) write a card for ``document`` and a description for each section, and file it under a
        topic. The logic is ``loamz.engine.documents.enrichment.enrich``, which runs over any ``DocumentLibrary``; this passes the library's
        enricher and timeout."""
        return await enrich_document(
            self,
            collection=collection,
            document=document,
            enricher=enricher or self._enricher,
            force=force,
            timeout=self._enrich_timeout,
        )

    async def brief(self, *, collection: str, document: str) -> DocumentBrief | None:
        collection = collection.strip("/")
        info = await self.info(collection, document)
        if info is None:
            return None
        rows = await self._sections_of(collection, document)
        return DocumentBrief(
            document=document,
            title=info.title,
            filename=info.filename,
            pages=info.pages,
            sections=tuple(
                SectionBrief(
                    int(r["position"]),
                    r["title"],
                    tuple(json.loads(r["heading_path"])),
                    int(r["first_page"]),
                    int(r["last_page"]),
                    int(r["tokens"]),
                    r["text"],
                )
                for r in rows
            ),
        )

    async def enrichment_state(self, *, collection: str, document: str) -> EnrichmentState | None:
        row = await self._state(collection.strip("/"), document)
        if row is None:
            return None
        return EnrichmentState(
            state=row["state"],
            enricher=row["enricher"],
            version=row["version"],
            attempts=int(row["attempts"]),
            updated_at=float(row["updated_at"]),
        )

    async def claim(
        self, *, collection: str, document: str, enricher: Enricher, attempts: int
    ) -> None:
        await self._set_state(collection.strip("/"), document, "running", enricher, attempts)

    async def fail(
        self,
        *,
        collection: str,
        document: str,
        enricher: Enricher,
        attempts: int,
        error: str,
    ) -> None:
        await self._set_state(
            collection.strip("/"), document, "failed", enricher, attempts, error=error
        )

    async def topic_tree(self, *, collection: str) -> list[str]:
        return await self._tree(collection.strip("/"))

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
        collection = collection.strip("/")
        info = await self.info(collection, document)
        brief = await self.brief(collection=collection, document=document)
        if info is None or brief is None:
            raise DocumentError(f"there is no document {collection}/{document}")
        rows = await self._sections_of(collection, document)
        by_section = dict(sections)
        before = await self._topics_of(collection, document)
        await self._write_enrichment_files(
            collection, document, info, brief, by_section, card, topics, enricher
        )
        await self._save_enrichment(
            collection, document, card, by_section, topics, enricher, attempts, usage
        )
        await self._reembed(collection, document, info, rows, by_section)
        await self._write_topic_pages(collection, [*before, *topics])
        await self._write_collection_index(collection)

    async def pending(
        self, *, collection: str, enricher: Enricher | None = None, limit: int = 50
    ) -> list[str]:
        """Documents in ``collection`` that still need ``enrich``: never described, failed fewer than three times, stuck running, or
        described by another enricher or version. The background sweep calls this."""
        collection = collection.strip("/")
        chosen = enricher or self._enricher
        name, version = (chosen.name, chosen.version) if chosen else ("", "")

        async def op(tx: Tx) -> list[str]:
            rows = await tx.fetchall(
                "SELECT d.document FROM library_documents d "
                "LEFT JOIN library_enrichment_state e ON e.collection = d.collection AND e.document = d.document "
                "WHERE d.collection = ? AND (e.document IS NULL "
                "OR (e.state = 'failed' AND e.attempts < 3) "
                "OR (e.state = 'running' AND e.updated_at < ?) "
                "OR (e.state = 'done' AND (e.enricher != ? OR e.version != ?))) "
                "ORDER BY d.seq LIMIT ?",
                collection, time.time() - STALE_RUNNING_S, name, version, limit,
            )  # fmt: skip
            return [r["document"] for r in rows]

        return await self._run(op)

    async def pending_all(
        self,
        *,
        enricher: Enricher | None = None,
        limit: int = 100,
        matching: str = "%",
        excluding: str | None = None,
    ) -> list[tuple[str, str]]:
        """``(collection, document)`` for documents in any collection that still need ``enrich`` (see ``pending``): what a startup sweep hands
        to the background queue. ``matching`` and ``excluding`` are SQL ``LIKE`` patterns on the collection, so a library sweeps only its own
        kind of collection (the same store can hold several libraries' documents)."""
        chosen = enricher or self._enricher
        name, version = (chosen.name, chosen.version) if chosen else ("", "")

        async def op(tx: Tx) -> list[tuple[str, str]]:
            skip = " AND d.collection NOT LIKE ?" if excluding else ""
            rows = await tx.fetchall(
                "SELECT d.collection, d.document FROM library_documents d "
                "LEFT JOIN library_enrichment_state e ON e.collection = d.collection AND e.document = d.document "
                f"WHERE d.collection LIKE ?{skip} AND (e.document IS NULL "  # noqa: S608 — fixed fragment
                "OR (e.state = 'failed' AND e.attempts < 3) "
                "OR (e.state = 'running' AND e.updated_at < ?) "
                "OR (e.state = 'done' AND (e.enricher != ? OR e.version != ?))) "
                "ORDER BY d.seq LIMIT ?",
                matching, *([excluding] if excluding else []), time.time() - STALE_RUNNING_S, name, version, limit,
            )  # fmt: skip
            return [(r["collection"], r["document"]) for r in rows]

        return await self._run(op)

    async def needs_reorganising(self, collection: str) -> bool:
        """Whether the topic tree has outgrown browsing: some topic has more than ``MAX_TOPIC_CHILDREN`` subtopics, or twice that many
        documents filed straight into it. Filing one document at a time causes this; ``reorganise`` repairs it."""
        collection = collection.strip("/")
        if not self._file_topics:
            return False

        async def op(tx: Tx) -> list[Any]:
            return list(
                await tx.fetchall(
                    "SELECT topic, document FROM library_topics WHERE collection = ?",
                    collection,
                )
            )

        rows = await self._run(op)
        children: dict[str, set[str]] = defaultdict(set)
        here: dict[str, int] = defaultdict(int)
        for r in rows:
            parts = r["topic"].split("/")
            here[r["topic"]] += 1
            for i in range(len(parts)):
                children["/".join(parts[:i])].add("/".join(parts[: i + 1]))
        return any(len(c) > MAX_TOPIC_CHILDREN for c in children.values()) or any(
            n > 2 * MAX_TOPIC_CHILDREN for n in here.values()
        )

    async def reorganise(
        self, *, collection: str, organiser: Organiser | None = None
    ) -> Reorganised:
        """Redraw the topic tree of ``collection`` in one pass with ``organiser`` (default: the library's). The logic is
        ``loamz.engine.documents.enrichment.reorganise``, which runs over any ``DocumentLibrary``."""
        return await reorganise_collection(
            self,
            collection=collection,
            organiser=organiser or self._organiser,
            timeout=self._enrich_timeout,
        )

    async def set_topics(
        self, *, collection: str, plan: Mapping[str, Sequence[str]], by: str
    ) -> None:
        collection = collection.strip("/")
        before = {d: await self._topics_of(collection, d) for d in plan}
        for document, topics in plan.items():
            await self._set_topics(collection, document, topics)
        await self._write_topic_pages(collection, [t for d in plan for t in (*before[d], *plan[d])])
        await self._write_collection_index(collection)
        await self._log_reorganisation(
            collection, by, before, {d: tuple(t) for d, t in plan.items()}
        )

    async def _set_topics(self, collection: str, document: str, topics: Sequence[str]) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM library_topics WHERE collection = ? AND document = ?",
                collection, document,
            )  # fmt: skip
            for topic in topics:
                await tx.execute(
                    "INSERT INTO library_topics (collection, document, topic) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    collection, document, topic,
                )  # fmt: skip

        await self._run(op)
        key = f"{collection}/{document}/index.md"
        index = okf.parse((await self._files.download(key)).decode("utf-8", "replace"))
        index.extra["topics"] = list(topics)
        await self._files.upload(
            key, okf.serialize(index).encode("utf-8"), content_type="text/markdown"
        )

    async def _log_reorganisation(
        self,
        collection: str,
        by: str,
        before: Mapping[str, Sequence[str]],
        plan: Mapping[str, Sequence[str]],
    ) -> None:
        """Append what changed to ``_topics/log.md``, so a reorganisation can be read back and undone by hand."""
        key = f"{collection}/{TOPICS_DIR}/log.md"
        try:
            text = (await self._files.download(key)).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — no log yet
            text = "# Topic log\n"
        lines = [f"\n## {okf.utc_now_iso()} — {by}\n"]
        lines += [
            f"- {document}: {', '.join(before[document]) or '(none)'} → {', '.join(topics)}"
            for document, topics in plan.items()
        ]
        await self._files.upload(
            key,
            (text.rstrip("\n") + "\n" + "\n".join(lines) + "\n").encode("utf-8"),
            content_type="text/markdown",
        )

    async def browse(self, *, collection: str, topic: str = "") -> Topic | None:
        """One level of the topic tree: the topics directly under ``topic`` (``""`` is the top) with how many documents each holds, and the
        documents filed at ``topic`` itself. ``None`` if no such topic."""
        collection, topic = collection.strip("/"), topic_path(topic)

        async def op(tx: Tx) -> Topic | None:
            rows = await tx.fetchall(
                "SELECT topic, document FROM library_topics WHERE collection = ? ORDER BY seq",
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
            docs: list[DocumentInfo] = []
            for document in here[:MAX_LIST]:
                row = await tx.fetchone(
                    "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                    collection, document,
                )  # fmt: skip
                if row is not None:
                    docs.append(self._doc(row))
            docs = await self._decorate(tx, collection, docs)
            return Topic(
                topic,
                tuple(sorted((p, len(d)) for p, d in children.items())),
                tuple(docs),
                more=max(0, len(here) - MAX_LIST),
            )

        return await self._run(op)

    async def _reembed(
        self,
        collection: str,
        document: str,
        info: DocumentInfo,
        rows: Sequence[Any],
        by_section: dict[int, str],
    ) -> None:
        """Embed the document's text chunks again with each section's description in front of them (a chunk that says "the deadline is
        30 June" is then found by what its section is about). Figures keep their vectors."""
        if self._embedder is None:
            return
        sections = [
            Section(
                r["title"],
                tuple(json.loads(r["heading_path"])),
                int(r["first_page"]),
                int(r["last_page"]),
                r["text"],
            )
            for r in rows
        ]
        await self._store.vectors.delete_where(
            collection=collection, filter={"document": document, "kind": "text"}
        )
        await self._index_vectors(
            collection, document, info.title, info.filename, sections, (), info.meta,
            descriptions=by_section,
        )  # fmt: skip

    # -- enrichment: storage

    async def _state(self, collection: str, document: str) -> Any:
        async def op(tx: Tx) -> Any:
            return await tx.fetchone(
                "SELECT * FROM library_enrichment_state WHERE collection = ? AND document = ?",
                collection, document,
            )  # fmt: skip

        return await self._run(op)

    async def _sections_of(self, collection: str, document: str) -> list[Any]:
        async def op(tx: Tx) -> list[Any]:
            return list(
                await tx.fetchall(
                    "SELECT position, title, heading_path, first_page, last_page, tokens, text FROM library_sections "
                    "WHERE collection = ? AND document = ? ORDER BY position",
                    collection, document,
                )
            )  # fmt: skip

        return await self._run(op)

    async def _topics_of(self, collection: str, document: str) -> list[str]:
        async def op(tx: Tx) -> list[str]:
            rows = await tx.fetchall(
                "SELECT topic FROM library_topics WHERE collection = ? AND document = ? ORDER BY seq",
                collection, document,
            )  # fmt: skip
            return [r["topic"] for r in rows]

        return await self._run(op)

    async def _tree(self, collection: str) -> list[str]:
        """The filing tree so far, most-used topics first, bounded: what an enricher is shown so it prefers an existing topic."""

        async def op(tx: Tx) -> list[str]:
            rows = await tx.fetchall(
                "SELECT topic, COUNT(*) AS n FROM library_topics WHERE collection = ? GROUP BY topic ORDER BY n DESC, topic LIMIT ?",
                collection, TOPIC_TREE_LIMIT,
            )  # fmt: skip
            return [r["topic"] for r in rows]

        return await self._run(op)

    async def _set_state(
        self,
        collection: str,
        document: str,
        state: str,
        enricher: Enricher,
        attempts: int,
        *,
        error: str | None = None,
        usage: EnrichmentUsage | None = None,
    ) -> None:
        usage = usage or EnrichmentUsage()

        async def op(tx: Tx) -> None:
            await tx.execute(
                _UPSERT_STATE,
                *_state_args(collection, document, state, enricher, attempts, error, usage),
            )

        await self._run(op)

    async def _save_enrichment(
        self,
        collection: str,
        document: str,
        card: str,
        by_section: dict[int, str],
        topics: Sequence[str],
        enricher: Enricher,
        attempts: int,
        usage: EnrichmentUsage,
    ) -> None:
        async def op(tx: Tx) -> None:
            for table in ("library_enrichment", "library_topics"):
                await tx.execute(
                    f"DELETE FROM {table} WHERE collection = ? AND document = ?",
                    collection, document,
                )  # noqa: S608 — fixed names  # fmt: skip
            for position, text in [(0, card), *sorted(by_section.items())]:
                await tx.execute(
                    "INSERT INTO library_enrichment (collection, document, position, description) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (collection, document, position) DO UPDATE SET description = excluded.description",
                    collection, document, position, text,
                )  # fmt: skip
            for topic in topics:
                await tx.execute(
                    "INSERT INTO library_topics (collection, document, topic) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    collection, document, topic,
                )  # fmt: skip
            await tx.execute(
                _UPSERT_STATE,
                *_state_args(collection, document, "done", enricher, attempts, None, usage),
            )

        await self._run(op)

    async def _restore_enrichment(
        self,
        collection: str,
        document: str,
        index: okf.Concept,
        written: dict[int, okf.Concept],
    ) -> None:
        """After a ``reindex``: put back the descriptions the bundle's files carry, so the catalog is again a pure function of the bundle."""
        meta = index.extra.get("enrichment")
        if not isinstance(meta, dict) or not index.description:
            return
        by_section = {n: c.description for n, c in written.items() if c.generated and c.description}
        topics = [topic_path(str(t)) for t in index.extra.get("topics") or []]

        class _Author:
            name = str(meta.get("enricher", "unknown"))
            version = str(meta.get("version", "0"))

        await self._save_enrichment(
            collection, document, index.description, by_section,
            [t for t in topics if t], _Author(), 1, EnrichmentUsage(),
        )  # type: ignore[arg-type]  # fmt: skip

    # -- enrichment: the files

    async def _write_enrichment_files(
        self,
        collection: str,
        document: str,
        info: DocumentInfo,
        brief: DocumentBrief,
        by_section: dict[int, str],
        card: str,
        topics: Sequence[str],
        enricher: Enricher,
    ) -> None:
        """Put the descriptions into the OKF files. Only ``description``, ``generated`` and a few frontmatter keys change; every section's body
        is written back exactly as it was read."""
        base = f"{collection}/{document}"
        now = okf.utc_now_iso()
        actor = okf.agent_actor(enricher.name, enricher.version)
        for section in brief.sections:
            key = f"{base}/{section.position:02d}-{slug(section.title)}.md"
            try:
                concept = okf.parse((await self._files.download(key)).decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 — a section file removed by hand has nothing to describe
                continue
            concept.description = by_section[section.position]
            concept.generated = {"by": actor, "at": now}
            await self._files.upload(
                key,
                okf.serialize(concept).encode("utf-8"),
                content_type="text/markdown",
            )
        index_key = f"{base}/index.md"
        index = okf.parse((await self._files.download(index_key)).decode("utf-8", "replace"))
        index.description = card
        index.generated = {"by": actor, "at": now}
        index.extra.update(
            {
                "sections": info.sections,
                "images": info.images,
                "topics": list(topics),
                "enrichment": {
                    "enricher": enricher.name,
                    "version": enricher.version,
                    "state": "done",
                    "at": now,
                },
            }
        )
        index.body = _describe_outline(
            index.body,
            by_section,
            {
                s.position: f"{pages_label(s.first_page, s.last_page)}, ~{s.tokens} tokens"
                for s in brief.sections
            },
        )
        await self._files.upload(
            index_key,
            okf.serialize(index).encode("utf-8"),
            content_type="text/markdown",
        )

    async def _write_topic_pages(self, collection: str, changed: Sequence[str]) -> None:
        """Rewrite the topic pages a change touched (the changed topics and every parent up to the top); remove pages that became empty."""
        if not self._file_topics or not changed:
            return
        affected = {""}
        for topic in changed:
            parts = topic.split("/")
            affected.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))

        async def load(tx: Tx) -> tuple[list[Any], dict[str, DocumentInfo]]:
            rows = list(
                await tx.fetchall(
                    "SELECT topic, document FROM library_topics WHERE collection = ? ORDER BY seq",
                    collection,
                )
            )
            docs: dict[str, DocumentInfo] = {}
            for document in {r["document"] for r in rows}:
                row = await tx.fetchone(
                    "SELECT * FROM library_documents WHERE collection = ? AND document = ?",
                    collection, document,
                )  # fmt: skip
                if row is not None:
                    docs[document] = self._doc(row)
            decorated = await self._decorate(tx, collection, list(docs.values()))
            return rows, {d.document: d for d in decorated}

        rows, docs = await self._run(load)
        for path in sorted(affected):
            here = [r["document"] for r in rows if r["topic"] == path]
            below: dict[str, set[str]] = defaultdict(set)
            for r in rows:
                t = r["topic"]
                if path and not t.startswith(path + "/"):
                    continue
                rest = t[len(path) :].lstrip("/") if path else t
                if rest and (t != path):
                    below[(path + "/" if path else "") + rest.split("/", 1)[0]].add(r["document"])
            folder = "/".join(slug(p) for p in path.split("/")) if path else ""
            key = f"{collection}/{TOPICS_DIR}/{folder + '/' if folder else ''}index.md"
            if not here and not below:
                await self._files.delete(key)
                continue
            depth = len(path.split("/")) if path else 0
            up = "../" * (depth + 1)
            lines: list[str] = []
            if below:
                lines += ["## Topics", ""]
                for child, documents in sorted(below.items()):
                    name = child.rsplit("/", 1)[-1]
                    lines.append(
                        f"- [{name}]({slug(name)}/index.md) — {len(documents)} document{'s' if len(documents) != 1 else ''}"
                    )
                lines.append("")
            if here:
                lines += ["## Documents", ""]
                for document in here:
                    d = docs.get(document)
                    if d is not None:
                        lines.append(f"- [{d.title}]({up}{document}/index.md) — {_blurb(d)}")
            total = len({d for ds in below.values() for d in ds} | set(here))
            concept = okf.Concept(
                type="Index",
                title=path.rsplit("/", 1)[-1] if path else "Topics",
                description=f"{total} document{'s' if total != 1 else ''}",
                body="\n".join(lines).strip(),
            )
            await self._files.upload(
                key,
                okf.serialize(concept).encode("utf-8"),
                content_type="text/markdown",
            )

    # ----------------------------------------------------------------------------------------------------- the vectors

    async def _index_vectors(
        self,
        collection: str,
        document: str,
        title: str,
        filename: str,
        sections: Sequence[Section],
        images: Sequence[BundleImage],
        metadata: dict[str, Any],
        descriptions: dict[int, str] | None = None,
    ) -> int:
        """Cut each section into chunks (and take its figures that carry their bytes), embed them, and store them under ``collection``. A chunk
        whose embedding fails is stored without one. Returns how many were stored without. Nothing here fails the add for a service that is
        down; a collection written by another embedder raises ``VectorSpaceError``."""
        embedder = self._embedder
        if embedder is None:
            return 0
        size = self._chunk_tokens or chunk_size_for(embedder.max_input_tokens)
        if descriptions:  # the description is embedded with each chunk, so leave room for it
            size = max(64, size - 100)
        base = {
            "document": document,
            "title": title,
            "filename": filename,
            **{k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))},
        }
        pending: list[
            tuple[Document, Any]
        ] = []  # a document, and what to embed for it: a string, or content blocks (a figure)
        for n, section in enumerate(sections, start=1):
            for i, chunk in enumerate(
                chunk_section(section.markdown, heading=section.title, max_tokens=size)
            ):
                doc = Document.from_text(
                    chunk.text,
                    id=f"{document}:{n}:{i}",
                    metadata={
                        **base,
                        "kind": "text",
                        "section": n,
                        "chunk": i,
                        "pages": [chunk.first_page, chunk.last_page],
                        "heading": section.title,
                        "heading_path": list(section.heading_path),
                    },
                )
                pending.append(
                    (
                        doc,
                        chunk.embedding_text(
                            title,
                            section.heading_path,
                            (descriptions or {}).get(n, ""),
                        ),
                    )
                )
        if Modality.IMAGE in embedder.modalities:
            for image in images:
                if image.data is None:
                    continue
                caption = image.caption or ""
                doc = Document.from_text(
                    caption or f"{image.label} on page {image.page}",
                    id=f"{document}:img:{image.id}",
                    metadata={
                        **base,
                        "kind": "image",
                        "image": image.id,
                        "pages": [image.page, image.page],
                        "label": image.label,
                    },
                )
                blocks: list[Any] = [MediaBlock.image(data=image.data, media_type=image.media_type)]
                if caption:
                    blocks.append(TextBlock(text=caption))
                pending.append((doc, blocks))
        return await self._embed_and_store(collection, pending)

    async def _embed_and_store(self, collection: str, pending: list[tuple[Document, Any]]) -> int:
        assert self._embedder is not None
        stored: list[Document] = []
        for start in range(0, len(pending), EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            vectors = await self._embed_batch([item for _doc, item in batch])
            stored.extend(
                doc.model_copy(update={"embedding": vector})
                for (doc, _item), vector in zip(batch, vectors, strict=True)
            )
        embedded = sum(1 for d in stored if d.embedding is not None)
        if stored:
            await self._store.vectors.upsert(
                stored,
                collection=collection,
                space=(self._embedder.model or None) if embedded else None,
            )
        return len(stored) - embedded

    async def _embed_batch(self, inputs: list[Any]) -> list[list[float] | None]:
        """One vector per input, or ``None`` for an input that could not be embedded: a batch that fails is retried one by one, so a
        single over-long chunk costs one vector, not the batch; a service that is down costs them all."""
        assert self._embedder is not None
        try:
            return list((await self._embedder.embed(inputs)).embeddings)
        except ServiceUnavailableError as exc:
            logger.warning(
                "embedding service unavailable: %d passages stored without vectors (%s)",
                len(inputs),
                exc,
            )
            return [None] * len(inputs)
        except ContextLengthError:
            out: list[list[float] | None] = []
            for item in inputs:
                try:
                    out.append((await self._embedder.embed([item])).embeddings[0])
                except (ContextLengthError, ServiceUnavailableError) as exc:
                    logger.warning(
                        "a passage could not be embedded and is stored without a vector: %s",
                        exc,
                    )
                    out.append(None)
            return out

    async def embed_missing(self, *, collection: str) -> int:
        """Embed the chunks that were stored without a vector (the embedding service was down when they were added). Returns how many
        still have none — 0 when all are done, the rest when the service is still down."""
        collection = collection.strip("/")
        if self._embedder is None:
            raise ValueError("this library has no embedder")
        vectors = self._store.vectors
        while True:
            batch = await vectors.unembedded(collection=collection, limit=EMBED_BATCH * 4)
            if not batch:
                return 0
            if await self._embed_and_store(
                collection, [(doc, _embedding_input(doc)) for doc in batch]
            ):
                return len(
                    await vectors.unembedded(collection=collection, limit=1_000_000)
                )  # the service is still down: stop, say how many

    # ------------------------------------------------------------------------------------------------------- rebuilding

    async def reindex(self, *, collection: str, missing_only: bool = False) -> int:
        """Rebuild ``collection``'s catalog from its bundle (after the catalog is lost or the bundle was edited by hand), and with an
        embedder its chunks and vectors too. Returns the number of documents indexed.

        ``missing_only=True`` changes nothing in the catalog: it only embeds the chunks that were stored without a vector, and returns how
        many were still without one afterwards."""
        collection = collection.strip("/")
        if missing_only:
            return await self.embed_missing(collection=collection)
        entries = await self._files.list_prefix(collection + "/")
        by_doc: dict[str, list[str]] = {}
        for key, _size, _mtime in entries:
            relative = key[len(collection) + 1 :]
            if "/" in relative:
                by_doc.setdefault(relative.split("/", 1)[0], []).append(relative.split("/", 1)[1])

        async def clear(tx: Tx) -> None:
            for table in (
                "library_sections",
                "library_images",
                "library_documents",
                *_ENRICHMENT_TABLES,
            ):
                await tx.execute(f"DELETE FROM {table} WHERE collection = ?", collection)  # noqa: S608

        await self._run(clear)
        count = 0
        for document, names in sorted(by_doc.items()):
            if document == TOPICS_DIR or "index.md" not in names:
                continue
            await self._index_filed(collection, document, names)
            count += 1
        if count:
            await self._write_collection_index(collection)
        return count


def _embedding_input(doc: Document) -> Any:
    """What to embed for a stored chunk that has no vector: its text under its title and heading trail (a figure's caption alone)."""
    meta = doc.metadata
    if meta.get("kind") == "image":
        return doc.to_text()
    trail = " › ".join(
        str(part) for part in (meta.get("title", ""), *meta.get("heading_path", [])) if part
    )
    return f"{trail}\n\n{doc.to_text()}" if trail else doc.to_text()


def tx_dialect(library: Library) -> str:
    return library._store.database.dialect


def _page_of(name: str) -> int:
    match = re.search(r"-p(\d+)-", name)
    return int(match.group(1)) if match else 1


_SECTION_LINE = re.compile(r"^(\d+)\. \[(.+?)\]\((.+?)\) — .*$")


def _describe_outline(body: str, descriptions: dict[int, str], stats: dict[int, str]) -> str:
    """A document index's ``## Sections`` lines with each section's description in place of the bare counts (which move to the end)."""
    lines = []
    for line in body.splitlines():
        match = _SECTION_LINE.match(line)
        if match and int(match.group(1)) in descriptions:
            n = int(match.group(1))
            line = f"{n}. [{match.group(2)}]({match.group(3)}) — {descriptions[n]} ({stats[n]})"
        lines.append(line)
    return "\n".join(lines)


def _blurb(doc: DocumentInfo) -> str:
    """One short line about a document for an index: its card's first sentence, else what can be counted."""
    if doc.description:
        first = sentences(doc.description)
        return clean(first[0] if first else doc.description, limit=160)
    return f"{doc.filename or doc.document}, {doc.pages} page{'s' if doc.pages != 1 else ''}"


_UPSERT_STATE = (
    "INSERT INTO library_enrichment_state (collection, document, state, enricher, version, attempts, error, input_tokens, output_tokens, cost_usd, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (collection, document) DO UPDATE SET state = excluded.state, enricher = excluded.enricher, version = excluded.version, "
    "attempts = excluded.attempts, error = excluded.error, input_tokens = excluded.input_tokens, output_tokens = excluded.output_tokens, "
    "cost_usd = excluded.cost_usd, updated_at = excluded.updated_at"
)


def _state_args(
    collection: str,
    document: str,
    state: str,
    enricher: Enricher,
    attempts: int,
    error: str | None,
    usage: EnrichmentUsage,
) -> tuple[Any, ...]:
    return (
        collection, document, state, enricher.name, enricher.version, attempts, error,
        usage.input_tokens, usage.output_tokens, usage.cost_usd, time.time(),
    )  # fmt: skip


def _slice_pages(text: str, first: int, last: int) -> str:
    """The part of ``text`` from ``<!-- page first -->`` up to (not including) ``<!-- page last+1 -->``."""
    markers = [(int(m.group(1)), m.start()) for m in _PAGE.finditer(text)]
    start = next((pos for number, pos in markers if number >= first), None)
    if start is None:
        return ""
    stop = next((pos for number, pos in markers if number > last and pos > start), len(text))
    return text[start:stop].strip()


__all__ = [
    "Added",
    "DocumentError",
    "DocumentInfo",
    "Hit",
    "Hits",
    "Library",
    "Listing",
    "Outline",
    "Passage",
    "Picture",
    "SectionInfo",
    "Topic",
    "slug",
    "tokens",
]
