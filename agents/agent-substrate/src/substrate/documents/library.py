"""``Library`` — documents a model can navigate, kept as an Open Knowledge Format bundle with a catalog beside it.

::

    library = Library(store)                                  # files: store.files; reads with Reader()
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
documents, a company's handbook — and the tool (``DocumentsTool``) takes it from the run's scope, never from the model.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from substrate.documents import okf
from substrate.documents.reader import Reader
from substrate.documents.split import Section, split, tokens
from substrate.documents.types import ExtractionResult
from substrate.stores import textsearch
from substrate.stores.database import Database, Tx

if TYPE_CHECKING:
    from substrate.stores.files import FileStore
    from substrate.stores.store import Store

_COMPONENT = "library"
_CID = re.compile(r"\(cid:([^)\s]+)\)")
_SLUG = re.compile(r"[^a-z0-9]+")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_PAGE = re.compile(r"<!-- page (\d+) -->")
_PAGE_MARKER_LINE = re.compile(r"<!-- page \d+ -->")
MAX_READ_CHARS = 24_000
MAX_OUTLINE = 60
MAX_LIST = 50
MAX_IMAGE_BYTES = 4 * 1024 * 1024
INDEX_LISTING = 100


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


SCHEMA = [_schema]


class DocumentError(ValueError):
    """A document could not be added: it could not be read, or it has nothing readable in it."""


@dataclass(frozen=True)
class Added:
    document: str
    title: str
    filename: str
    pages: int
    sections: int
    images: int
    needs_ocr: tuple[int, ...] = ()
    duplicate: bool = False
    """The same file was already in the collection: nothing was written."""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentInfo:
    document: str
    title: str
    filename: str
    pages: int
    sections: int
    images: int
    needs_ocr: tuple[int, ...]
    engine: str
    resource: str | None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionInfo:
    position: int
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    tokens: int
    headings: tuple[str, ...] = ()
    """The headings inside the section, for a section that holds several."""


@dataclass(frozen=True)
class Outline:
    document: DocumentInfo
    sections: tuple[SectionInfo, ...]
    more: int = 0
    """Entries beyond ``MAX_OUTLINE`` that are not listed."""
    headings: tuple[tuple[int, str, int], ...] = ()
    """For one section: ``(level, heading, page)`` inside it."""


@dataclass(frozen=True)
class Passage:
    document: DocumentInfo
    section: SectionInfo
    text: str
    offset: int = 0
    next_offset: int | None = None
    """Where to continue (``offset=``) when the text was cut at ``MAX_READ_CHARS``; ``None`` at the end."""
    total_chars: int = 0


@dataclass(frozen=True)
class Hit:
    document: DocumentInfo
    section: SectionInfo
    snippet: str
    score: float


@dataclass(frozen=True)
class Listing:
    documents: tuple[DocumentInfo, ...]
    next: str | None = None
    """Pass as ``after=`` for the next page; ``None`` when this was the last."""


@dataclass(frozen=True)
class Picture:
    data: bytes
    media_type: str
    document: str
    image: str
    page: int
    label: str
    caption: str | None


def slug(text: str, *, limit: int = 40) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:limit].strip("-") or "untitled"


def _pages_label(first: int, last: int) -> str:
    return f"p. {first}" if first == last else f"pp. {first}–{last}"


def _headings_in(markdown: str) -> list[tuple[int, str, int]]:
    out = []
    page = 1
    for line in markdown.splitlines():
        marker = _PAGE.match(line)
        if marker:
            page = int(marker.group(1))
            continue
        heading = _HEADING.match(line)
        if heading:
            out.append((len(heading.group(1)), heading.group(2), page))
    return out


class Library:
    def __init__(self, store: Store, *, files: FileStore | None = None, reader: Reader | None = None) -> None:
        self._store = store
        self._files: FileStore = files if files is not None else store.files
        self._reader = reader or Reader()

    async def _run(self, fn):
        await self._store.ensure(_COMPONENT, SCHEMA)
        return await self._store.run(fn)

    @staticmethod
    def document_id(filename: str, sha256: str) -> str:
        """The id ``add`` gives a file: its name's slug and the first six characters of the SHA-256 of its bytes — so a caller that
        already has the checksum can find the document without asking."""
        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "document"
        return f"{slug(stem)}-{sha256[:6]}"

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
        not be read or has no readable text."""
        if isinstance(source, ExtractionResult):
            result, digest = source, sha256 or hashlib.sha256(source.markdown.encode("utf-8")).hexdigest()
        else:
            digest = hashlib.sha256(source).hexdigest()
            result = await self._reader.read(source, filename, content_type=content_type)
        if not result.success:
            raise DocumentError(result.error or "the document could not be read")
        images = [image for page in result.pages for image in page.images]
        text = _PAGE_MARKER_LINE.sub("", result.markdown).strip()
        if not text and not images:
            where = f" (pages {', '.join(map(str, result.needs_ocr))} are pictures of text and OCR is not available)" if result.needs_ocr else ""
            raise DocumentError(f"{filename or 'the document'} has no readable text{where}")

        stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "document"
        document = self.document_id(filename, digest)
        collection = collection.strip("/")
        if await self._exists(collection, document):
            info = await self.info(collection, document)
            assert info is not None
            return Added(document, info.title, info.filename, info.pages, info.sections, info.images, info.needs_ocr, duplicate=True)

        title = result.title or next((h[1] for h in _headings_in(result.markdown) if h[0] <= 2), "") or stem
        image_ids: dict[tuple[int, int], str] = {}
        for page in result.pages:
            for k, image in enumerate(page.images):
                image_ids[(page.page_number, k)] = image.id or f"img-p{page.page_number}-{k}"
        markdown = _CID.sub(lambda m: f"(images/{m.group(1)}.png)", result.markdown)
        sections = split(markdown, title=title) or [Section(title, (title,), 1, max(1, len(result.pages)), "")]
        base = f"{collection}/{document}"

        for page in result.pages:
            for k, image in enumerate(page.images):
                await self._files.upload(f"{base}/images/{image_ids[(page.page_number, k)]}.png", image.data, content_type=image.media_type)
        names = [f"{n:02d}-{slug(s.title)}.md" for n, s in enumerate(sections, start=1)]
        for n, (section, name) in enumerate(zip(sections, names, strict=True), start=1):
            body = _with_navigation(section, names, n)
            concept = okf.Concept(
                type="Section",
                title=section.title,
                description=f"{_pages_label(section.first_page, section.last_page)} · ~{section.tokens} tokens",
                resource=resource,
                body=body,
                extra={"document": document, "section": n, "parent": "index.md", "heading_path": list(section.heading_path), "pages": [section.first_page, section.last_page]},
            )
            await self._files.upload(f"{base}/{name}", okf.serialize(concept).encode("utf-8"), content_type="text/markdown")
        needs_ocr = list(result.needs_ocr)
        pages = max([p.page_number for p in result.pages] or [1])
        index = okf.Concept(
            type="Index",
            title=title,
            description=f"{filename or document} · {pages} page{'s' if pages != 1 else ''} · {len(sections)} sections · {len(images)} images",
            resource=resource,
            body=_document_index(sections, names, image_ids, result),
            extra={
                "document": document,
                "filename": filename,
                "engine": result.engine,
                "sha256": digest,
                "pages": pages,
                "needs_ocr": needs_ocr,
                "metadata": metadata or {},
            },
        )
        await self._files.upload(f"{base}/index.md", okf.serialize(index).encode("utf-8"), content_type="text/markdown")

        info = DocumentInfo(document, title, filename, pages, len(sections), len(images), tuple(needs_ocr), result.engine, resource, metadata or {})
        await self._catalog(collection, info, digest, sections, [(image_ids[(p.page_number, k)], p.page_number, i.label.value, i.caption, i.media_type) for p in result.pages for k, i in enumerate(p.images)])
        await self._write_collection_index(collection)
        return Added(document, title, filename, pages, len(sections), len(images), tuple(needs_ocr), warnings=tuple(result.warnings))

    async def _exists(self, collection: str, document: str) -> bool:
        async def op(tx: Tx) -> bool:
            row = await tx.fetchone("SELECT 1 AS found FROM library_documents WHERE collection = ? AND document = ?", collection, document)
            return row is not None

        return await self._run(op)

    async def _catalog(self, collection: str, info: DocumentInfo, digest: str, sections: Sequence[Section], images: Sequence[tuple[str, int, str, str | None, str]]) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute("DELETE FROM library_sections WHERE collection = ? AND document = ?", collection, info.document)
            await tx.execute("DELETE FROM library_images WHERE collection = ? AND document = ?", collection, info.document)
            await tx.execute("DELETE FROM library_documents WHERE collection = ? AND document = ?", collection, info.document)
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
        lines = [f"- [{d.title}]({d.document}/index.md) — {d.filename or d.document}, {d.pages} page{'s' if d.pages != 1 else ''}" for d in listing.documents]
        if total > len(lines):
            lines.append(f"- … and {total - len(lines)} more")
        concept = okf.Concept(type="Index", title="Documents", description=f"{total} document{'s' if total != 1 else ''}", body="\n".join(lines))
        await self._files.upload(f"{collection}/index.md", okf.serialize(concept).encode("utf-8"), content_type="text/markdown")

    async def _count(self, collection: str) -> int:
        async def op(tx: Tx) -> int:
            row = await tx.fetchone("SELECT COUNT(*) AS n FROM library_documents WHERE collection = ?", collection)
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
            row = await tx.fetchone("SELECT * FROM library_documents WHERE collection = ? AND document = ?", collection, document)
            return self._doc(row) if row else None

        return await self._run(op)

    async def list(self, *, collection: str, limit: int = MAX_LIST, after: str | None = None) -> Listing:
        """The documents of ``collection``, oldest first, ``limit`` (at most ``MAX_LIST``) at a time."""
        collection, limit = collection.strip("/"), max(1, min(limit, MAX_LIST))
        cursor = int(after) if after and after.isdigit() else 0

        async def op(tx: Tx) -> tuple[list[DocumentInfo], int | None]:
            rows = await tx.fetchall(
                "SELECT * FROM library_documents WHERE collection = ? AND seq > ? ORDER BY seq LIMIT ?", collection, cursor, limit + 1
            )
            page = rows[:limit]
            return [self._doc(r) for r in page], (int(page[-1]["seq"]) if len(rows) > limit else None)

        documents, last = await self._run(op)
        return Listing(tuple(documents), str(last) if last is not None else None)

    async def outline(self, *, collection: str, document: str, section: int | None = None) -> Outline | None:
        """A document's sections — or, with ``section``, the headings inside that one. ``None`` if there is no such document or section."""
        collection = collection.strip("/")

        async def op(tx: Tx) -> Outline | None:
            doc_row = await tx.fetchone("SELECT * FROM library_documents WHERE collection = ? AND document = ?", collection, document)
            if doc_row is None:
                return None
            info = self._doc(doc_row)
            if section is not None:
                row = await tx.fetchone(
                    "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND position = ?", collection, document, section
                )
                if row is None:
                    return None
                headings = _headings_in(row["text"])
                return Outline(info, (self._section(row),), more=max(0, len(headings) - MAX_OUTLINE), headings=tuple(headings[:MAX_OUTLINE]))
            rows = await tx.fetchall(
                "SELECT position, title, heading_path, first_page, last_page, tokens, text FROM library_sections "
                "WHERE collection = ? AND document = ? ORDER BY position LIMIT ?",
                collection, document, MAX_OUTLINE,
            )  # fmt: skip
            sections = []
            for row in rows:
                inside = tuple(h[1] for h in _headings_in(row["text"]) if h[1] != row["title"])[:6]
                sections.append(SectionInfo(**{**self._section(row).__dict__, "headings": inside}))
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
            doc_row = await tx.fetchone("SELECT * FROM library_documents WHERE collection = ? AND document = ?", collection, document)
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
                    "SELECT * FROM library_sections WHERE collection = ? AND document = ? AND position = ?", collection, document, section or 1
                )
                rows = [row] if row else []
                text = row["text"] if row else ""
            if not rows or not text.strip():
                return None
            offset_ = max(0, min(offset, len(text)))
            chunk = text[offset_ : offset_ + MAX_READ_CHARS]
            end = offset_ + len(chunk)
            return Passage(self._doc(doc_row), self._section(rows[0]), chunk, offset_, end if end < len(text) else None, len(text))

        return await self._run(op)

    async def find(self, *, collection: str, query: str, document: str | None = None, limit: int = 8) -> list[Hit]:
        """Sections matching ``query`` — every word first, relaxing to any word when nothing has them all — best first."""
        collection, limit = collection.strip("/"), max(1, min(limit, 20))
        query_words = textsearch.terms(query)
        if not query_words:
            return []

        async def op(tx: Tx) -> list[Hit]:
            for mode in ("all", "any"):
                source, where, params = textsearch.ranked(
                    tx_dialect(self), index="library_sections_fts", table="library_sections", alias="s", query_words=query_words, match=mode
                )
                score = textsearch.score(tx_dialect(self), index="library_sections_fts", alias="s")
                sql = f"SELECT s.*, {score} AS score FROM {source} WHERE {where} AND s.collection = ?"
                args: list[Any] = [*params, collection]
                if document:
                    sql += " AND s.document = ?"
                    args.append(document)
                rows = await tx.fetchall(sql + " ORDER BY score DESC LIMIT ?", *args, limit)
                if rows:
                    hits = []
                    for row in rows:
                        doc_row = await tx.fetchone("SELECT * FROM library_documents WHERE collection = ? AND document = ?", collection, row["document"])
                        if doc_row is not None:
                            hits.append(Hit(self._doc(doc_row), self._section(row), textsearch.snippet(row["text"], query_words), float(row["score"])))
                    return hits
            return []

        return await self._run(op)

    async def view(self, *, collection: str, image: str | None = None, document: str | None = None, page: int | None = None) -> Picture | None:
        """One picture: by its id, or the first one on ``page`` of ``document``. ``None`` if there is none (or it is too large to show)."""
        collection = collection.strip("/")

        async def op(tx: Tx) -> Any:
            if image:
                sql, args = "SELECT * FROM library_images WHERE collection = ? AND image = ?", [collection, image]
                if document:
                    sql += " AND document = ?"
                    args.append(document)
                return await tx.fetchone(sql + " ORDER BY seq LIMIT 1", *args)
            if document and page is not None:
                return await tx.fetchone(
                    "SELECT * FROM library_images WHERE collection = ? AND document = ? AND page = ? ORDER BY seq LIMIT 1", collection, document, page
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
        return Picture(data, row["media_type"], row["document"], row["image"], int(row["page"]), row["label"], row["caption"])

    # -------------------------------------------------------------------------------------------------------- removing

    async def delete(self, *, collection: str, document: str) -> bool:
        """Remove one document — bundle and catalog. ``False`` if it was not there."""
        collection = collection.strip("/")
        existed = await self._exists(collection, document)
        await self._files.delete_prefix(f"{collection}/{document}/")

        async def op(tx: Tx) -> None:
            for table in ("library_sections", "library_images", "library_documents"):
                await tx.execute(f"DELETE FROM {table} WHERE collection = ? AND document = ?", collection, document)  # noqa: S608 — fixed names

        await self._run(op)
        if existed:
            await self._write_collection_index(collection)
        return existed

    async def erase_under(self, prefix: str) -> int:
        """Remove every document in every collection at or under ``prefix`` (a tenant leaving, a user erased): the catalog rows, each
        document's bundle, and each collection's index. Only what this library wrote — other files under ``prefix`` are not touched.
        Returns the number of documents removed."""
        prefix = prefix.strip("/")

        async def op(tx: Tx) -> list[tuple[str, str]]:
            rows = await tx.fetchall(
                "SELECT collection, document FROM library_documents WHERE collection = ? OR collection LIKE ?", prefix, prefix + "/%"
            )
            for table in ("library_sections", "library_images", "library_documents"):
                await tx.execute(f"DELETE FROM {table} WHERE collection = ? OR collection LIKE ?", prefix, prefix + "/%")  # noqa: S608
            return [(r["collection"], r["document"]) for r in rows]

        removed = await self._run(op)
        for collection, document in removed:
            await self._files.delete_prefix(f"{collection}/{document}/")
        for collection in {c for c, _d in removed}:
            await self._files.delete(f"{collection}/index.md")
        return len(removed)

    # ------------------------------------------------------------------------------------------------------- rebuilding

    async def reindex(self, *, collection: str) -> int:
        """Rebuild the catalog of ``collection`` from its bundle (after the catalog is lost or the bundle was edited by hand).
        Returns the number of documents indexed."""
        collection = collection.strip("/")
        entries = await self._files.list_prefix(collection + "/")
        by_doc: dict[str, list[str]] = {}
        for key, _size, _mtime in entries:
            relative = key[len(collection) + 1 :]
            if "/" in relative:
                by_doc.setdefault(relative.split("/", 1)[0], []).append(relative.split("/", 1)[1])

        async def clear(tx: Tx) -> None:
            for table in ("library_sections", "library_images", "library_documents"):
                await tx.execute(f"DELETE FROM {table} WHERE collection = ?", collection)  # noqa: S608

        await self._run(clear)
        count = 0
        for document, names in sorted(by_doc.items()):
            if "index.md" not in names:
                continue
            index = okf.parse((await self._files.download(f"{collection}/{document}/index.md")).decode("utf-8", "replace"))
            sections: list[Section] = []
            for name in sorted(n for n in names if n != "index.md" and "/" not in n and n.endswith(".md")):
                concept = okf.parse((await self._files.download(f"{collection}/{document}/{name}")).decode("utf-8", "replace"))
                pages = concept.extra.get("pages") or [1, 1]
                sections.append(Section(concept.title or name, tuple(concept.extra.get("heading_path") or ()), int(pages[0]), int(pages[-1]), _strip_navigation(concept.body)))
            images = [(n.rsplit("/", 1)[-1].removesuffix(".png"), _page_of(n), "figure", None, "image/png") for n in names if n.startswith("images/")]
            extra = index.extra
            info = DocumentInfo(
                document, index.title or document, str(extra.get("filename", "")), int(extra.get("pages", 1)), len(sections), len(images),
                tuple(int(p) for p in extra.get("needs_ocr", [])), str(extra.get("engine", "")), index.resource, dict(extra.get("metadata") or {}),
            )  # fmt: skip
            await self._catalog(collection, info, str(extra.get("sha256", "")), sections, images)
            count += 1
        if count:
            await self._write_collection_index(collection)
        return count


def tx_dialect(library: Library) -> str:
    return library._store.database.dialect


def _page_of(name: str) -> int:
    match = re.search(r"-p(\d+)-", name)
    return int(match.group(1)) if match else 1


def _with_navigation(section: Section, names: list[str], n: int) -> str:
    links = []
    if n > 1:
        links.append(f"[← Previous]({names[n - 2]})")
    links.append("[Up](index.md)")
    if n < len(names):
        links.append(f"[Next →]({names[n]})")
    return f"{section.markdown}\n\n---\n{' · '.join(links)}"


def _strip_navigation(body: str) -> str:
    head, sep, tail = body.rpartition("\n\n---\n")
    return head if sep and "](" in tail and "Up" in tail else body


def _document_index(sections: Sequence[Section], names: Sequence[str], image_ids: dict[tuple[int, int], str], result: ExtractionResult) -> str:
    lines = ["## Sections", ""]
    for n, (section, name) in enumerate(zip(sections, names, strict=True), start=1):
        lines.append(f"{n}. [{section.title}]({name}) — {_pages_label(section.first_page, section.last_page)}, ~{section.tokens} tokens")
    if image_ids:
        lines += ["", "## Images", ""]
        for page in result.pages:
            for k, image in enumerate(page.images):
                ident = image_ids[(page.page_number, k)]
                lines.append(f"- [{ident}](images/{ident}.png) — {image.label.value}, p. {page.page_number}" + (f": {image.caption}" if image.caption else ""))
    if result.needs_ocr:
        lines += ["", f"> Pages {', '.join(map(str, result.needs_ocr))} are pictures of text and were not read (no OCR was available)."]
    return "\n".join(lines)


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
    "Library",
    "Listing",
    "Outline",
    "Passage",
    "Picture",
    "SectionInfo",
    "slug",
    "tokens",
]
