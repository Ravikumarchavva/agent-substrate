"""``Bundle`` — a document made ready to file: the OKF files that will hold it and what the catalog will say about it.

``build_bundle`` is pure: it turns an ``ExtractionResult`` into sections, figures and the files of the bundle, and touches no
storage. ``Library.prepare`` is that step, ``Library.file`` writes ``Bundle.files``, ``Library.index`` builds the catalog. A bundle
read back from the files (``Library.index`` after the fact, ``reindex``) has no ``files`` and no figure bytes: it was already written.

::

    {collection}/{doc}/index.md                  type: Index  — the document's outline
    {collection}/{doc}/NN-{slug}.md              type: Section — one concept, with Prev/Up/Next links
    {collection}/{doc}/images/{id}.png           figures, charts, table crops
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from substrate.documents import okf
from substrate.documents.split import Section, split
from substrate.documents.types import DocumentError, ExtractionResult

_CID = re.compile(r"\(cid:([^)\s]+)\)")
_SLUG = re.compile(r"[^a-z0-9]+")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_PAGE = re.compile(r"<!-- page (\d+) -->")
_PAGE_MARKER_LINE = re.compile(r"<!-- page \d+ -->")


def slug(text: str, *, limit: int = 40) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:limit].strip("-") or "untitled"


def document_id(filename: str, sha256: str) -> str:
    """The id a document gets: its name's slug and the first six characters of the SHA-256 of its bytes — so a caller that already
    has the checksum can find the document without asking."""
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "document"
    return f"{slug(stem)}-{sha256[:6]}"


def pages_label(first: int, last: int) -> str:
    return f"p. {first}" if first == last else f"pp. {first}–{last}"


def headings_in(markdown: str) -> list[tuple[int, str, int]]:
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


def with_navigation(section: Section, names: list[str], n: int) -> str:
    links = []
    if n > 1:
        links.append(f"[← Previous]({names[n - 2]})")
    links.append("[Up](index.md)")
    if n < len(names):
        links.append(f"[Next →]({names[n]})")
    return f"{section.markdown}\n\n---\n{' · '.join(links)}"


def strip_navigation(body: str) -> str:
    head, sep, tail = body.rpartition("\n\n---\n")
    return head if sep and "](" in tail and "Up" in tail else body


@dataclass(frozen=True)
class BundleFile:
    path: str
    """Relative to ``{collection}/{document}/``."""
    data: bytes
    content_type: str


@dataclass(frozen=True)
class BundleImage:
    id: str
    page: int
    label: str
    caption: str | None
    media_type: str
    data: bytes | None = None
    """The picture's bytes; ``None`` for a bundle read back from files (they are not loaded)."""


@dataclass(frozen=True)
class Bundle:
    """One document, ready to be filed (``Library.file``) and indexed (``Library.index``)."""

    document: str
    title: str
    filename: str
    sha256: str
    resource: str | None
    engine: str
    pages: int
    sections: tuple[Section, ...]
    images: tuple[BundleImage, ...] = ()
    needs_ocr: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    files: tuple[BundleFile, ...] = ()
    """What ``Library.file`` writes, in order: figures, then sections, then the index."""
    warnings: tuple[str, ...] = ()


def build_bundle(
    result: ExtractionResult,
    *,
    filename: str,
    sha256: str,
    resource: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Bundle:
    """The bundle for ``result``. Raises ``DocumentError`` for a read that failed or has no readable text. No storage is touched."""
    if not result.success:
        raise DocumentError(result.error or "the document could not be read")
    pictures = [image for page in result.pages for image in page.images]
    text = _PAGE_MARKER_LINE.sub("", result.markdown).strip()
    if not text and not pictures:
        where = (
            f" (pages {', '.join(map(str, result.needs_ocr))} are pictures of text and OCR is not available)"
            if result.needs_ocr
            else ""
        )
        raise DocumentError(f"{filename or 'the document'} has no readable text{where}")

    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "document"
    document = document_id(filename, sha256)
    meta = metadata or {}
    title = (
        result.title
        or next((h[1] for h in headings_in(result.markdown) if h[0] <= 2), "")
        or stem
    )
    image_ids: dict[tuple[int, int], str] = {}
    images: list[BundleImage] = []
    for page in result.pages:
        for k, image in enumerate(page.images):
            ident = image.id or f"img-p{page.page_number}-{k}"
            image_ids[(page.page_number, k)] = ident
            images.append(
                BundleImage(
                    ident,
                    page.page_number,
                    image.label.value,
                    image.caption,
                    image.media_type,
                    image.data,
                )
            )
    markdown = _CID.sub(lambda m: f"(images/{m.group(1)}.png)", result.markdown)
    sections = split(markdown, title=title) or [
        Section(title, (title,), 1, max(1, len(result.pages)), "")
    ]

    files: list[BundleFile] = [
        BundleFile(f"images/{image.id}.png", image.data or b"", image.media_type)
        for image in images
    ]
    names = [f"{n:02d}-{slug(s.title)}.md" for n, s in enumerate(sections, start=1)]
    for n, (section, name) in enumerate(zip(sections, names, strict=True), start=1):
        concept = okf.Concept(
            type="Section",
            title=section.title,
            description=f"{pages_label(section.first_page, section.last_page)} · ~{section.tokens} tokens",
            resource=resource,
            body=with_navigation(section, names, n),
            extra={
                "document": document,
                "section": n,
                "parent": "index.md",
                "heading_path": list(section.heading_path),
                "pages": [section.first_page, section.last_page],
            },
        )
        files.append(
            BundleFile(name, okf.serialize(concept).encode("utf-8"), "text/markdown")
        )
    needs_ocr = tuple(result.needs_ocr)
    pages = max([p.page_number for p in result.pages] or [1])
    index = okf.Concept(
        type="Index",
        title=title,
        description=f"{filename or document} · {pages} page{'s' if pages != 1 else ''} · {len(sections)} sections · {len(pictures)} images",
        resource=resource,
        body=document_index(sections, names, image_ids, result),
        extra={
            "document": document,
            "filename": filename,
            "engine": result.engine,
            "sha256": sha256,
            "pages": pages,
            "needs_ocr": list(needs_ocr),
            "metadata": meta,
        },
    )
    files.append(
        BundleFile("index.md", okf.serialize(index).encode("utf-8"), "text/markdown")
    )
    return Bundle(
        document=document,
        title=title,
        filename=filename,
        sha256=sha256,
        resource=resource,
        engine=result.engine,
        pages=pages,
        sections=tuple(sections),
        images=tuple(images),
        needs_ocr=needs_ocr,
        metadata=meta,
        files=tuple(files),
        warnings=tuple(result.warnings),
    )


def document_index(
    sections: Sequence[Section],
    names: Sequence[str],
    image_ids: dict[tuple[int, int], str],
    result: ExtractionResult,
) -> str:
    lines = ["## Sections", ""]
    for n, (section, name) in enumerate(zip(sections, names, strict=True), start=1):
        lines.append(
            f"{n}. [{section.title}]({name}) — {pages_label(section.first_page, section.last_page)}, ~{section.tokens} tokens"
        )
    if image_ids:
        lines += ["", "## Images", ""]
        for page in result.pages:
            for k, image in enumerate(page.images):
                ident = image_ids[(page.page_number, k)]
                lines.append(
                    f"- [{ident}](images/{ident}.png) — {image.label.value}, p. {page.page_number}"
                    + (f": {image.caption}" if image.caption else "")
                )
    if result.needs_ocr:
        lines += [
            "",
            f"> Pages {', '.join(map(str, result.needs_ocr))} are pictures of text and were not read (no OCR was available).",
        ]
    return "\n".join(lines)


__all__ = [
    "Bundle",
    "BundleFile",
    "BundleImage",
    "build_bundle",
    "document_id",
    "headings_in",
    "pages_label",
    "slug",
    "strip_navigation",
    "with_navigation",
]
