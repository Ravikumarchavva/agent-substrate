"""Splitting a document's markdown into sections a model can navigate: one concept per section, sized to be read whole.

A section is cut where the document itself says one thing ends and another begins:

1. at the **shallowest heading level that gives at least two sections** (a lone ``#`` title is not a split);
2. sections under ``MIN_TOKENS`` are merged into their neighbour, so a heading with one line under it is not a file;
3. a section over ``HARD_MAX_TOKENS`` is split at its next heading level, else at page breaks, else at paragraphs;
4. a document with **no headings** is cut into groups of whole pages (a slide deck, a sheet, a scan) of about ``TARGET_MAX_TOKENS``.

Page markers (``<!-- page N -->``), tables and figure links stay inline, so every section knows its pages and a citation can name them.
Tokens are estimated as UTF-8 bytes ÷ 3 — an over-estimate for English, which is the safe side for a size cap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_TOKENS = 300
TARGET_MAX_TOKENS = 6000
HARD_MAX_TOKENS = 8000

_PAGE = re.compile(r"^<!-- page (\d+) -->\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def tokens(text: str) -> int:
    return len(text.encode("utf-8")) // 3 + 1


@dataclass(frozen=True)
class Section:
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    markdown: str

    @property
    def tokens(self) -> int:
        return tokens(self.markdown)


@dataclass
class _Line:
    text: str
    page: int
    level: int = 0
    """Heading level (1–6), or 0 for a line that is not a heading."""
    title: str = ""


def _lines(markdown: str) -> list[_Line]:
    out: list[_Line] = []
    page, fenced = 1, False
    for text in markdown.splitlines():
        marker = _PAGE.match(text)
        if marker:
            page = int(marker.group(1))
        if _FENCE.match(text):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(text)
        out.append(_Line(text, page, len(heading.group(1)) if heading else 0, heading.group(2) if heading else ""))
    return out


def _join(lines: list[_Line]) -> str:
    return "\n".join(line.text for line in lines).strip("\n")


def split(markdown: str, *, title: str = "Document") -> list[Section]:
    """``markdown`` as sections, in reading order. Never empty for non-empty text."""
    lines = _lines(markdown)
    if not any(line.text.strip() for line in lines):
        return []
    headings = [line for line in lines if line.level]
    by_level = {level: sum(1 for h in headings if h.level == level) for level in range(1, 7)}
    level = next((lv for lv in range(1, 7) if by_level[lv] >= 2), 0)
    chunks = _cut_at_level(lines, level) if level else _cut_by_pages(lines)
    sections = [s for chunk in chunks for s in _fit(chunk, level)]
    return _label(_merge_small(sections), title, by_pages=not level)


# ---------------------------------------------------------------------------------------------------------------- cutting


def _cut_at_level(lines: list[_Line], level: int) -> list[list[_Line]]:
    """Cut before every heading of ``level`` or shallower; what precedes the first cut is the preamble."""
    chunks: list[list[_Line]] = [[]]
    for line in lines:
        if line.level and line.level <= level and any(item.text.strip() and not _PAGE.match(item.text) for item in chunks[-1]):
            chunks.append([])
        chunks[-1].append(line)
    return [c for c in chunks if any(item.text.strip() and not _PAGE.match(item.text) for item in c)]


def _pages(lines: list[_Line]) -> list[list[_Line]]:
    pages: list[list[_Line]] = [[]]
    for line in lines:
        if _PAGE.match(line.text) and any(item.text.strip() for item in pages[-1]):
            pages.append([])
        pages[-1].append(line)
    return [p for p in pages if any(item.text.strip() for item in p)]


def _cut_by_pages(lines: list[_Line]) -> list[list[_Line]]:
    """Whole pages, grouped up to the target size."""
    groups: list[list[_Line]] = []
    size = 0
    for page in _pages(lines):
        cost = tokens(_join(page))
        if groups and size + cost > TARGET_MAX_TOKENS:
            groups.append([])
            size = 0
        if not groups:
            groups.append([])
        groups[-1].extend(page)
        size += cost
    return groups


def _fit(chunk: list[_Line], level: int) -> list[list[_Line]]:
    """A chunk within ``HARD_MAX_TOKENS``, or its pieces: split at the next heading level, then pages, then paragraphs."""
    if tokens(_join(chunk)) <= HARD_MAX_TOKENS:
        return [chunk]
    deeper = next(
        (lv for lv in range(level + 1, 7) if sum(1 for line in chunk if line.level == lv) >= 2),
        0,
    )
    if deeper:
        pieces = _cut_at_level(chunk, deeper)
        if len(pieces) > 1:
            return [p for piece in pieces for p in _fit(piece, deeper)]
    pieces = _pages(chunk)
    if len(pieces) > 1:
        grouped = _cut_by_pages(chunk)
        if len(grouped) > 1:
            return [p for piece in grouped for p in _fit(piece, level)]
    return _by_paragraphs(chunk)


def _by_paragraphs(chunk: list[_Line]) -> list[list[_Line]]:
    pieces: list[list[_Line]] = [[]]
    size = 0
    fenced = False
    for line in _cut_long_lines(chunk):
        if _FENCE.match(line.text):
            fenced = not fenced
        cost = tokens(line.text) + 1
        boundary = not fenced and not line.text.strip() and size >= TARGET_MAX_TOKENS
        if boundary:
            pieces.append([])
            size = 0
            continue
        if size + cost > HARD_MAX_TOKENS and size:  # a single paragraph longer than the cap: cut between lines
            pieces.append([])
            size = 0
        pieces[-1].append(line)
        size += cost
    return [p for p in pieces if any(item.text.strip() for item in p)]


def _cut_long_lines(chunk: list[_Line]) -> list[_Line]:
    """A single line over the cap (a minified blob, an unbroken OCR line) is cut at spaces; nothing else can make it fit."""
    limit = TARGET_MAX_TOKENS * 3
    out: list[_Line] = []
    for line in chunk:
        text = line.text
        while len(text.encode("utf-8")) > HARD_MAX_TOKENS * 3:
            cut = text.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(_Line(text[:cut], line.page))
            out.append(_Line("", line.page))
            text = text[cut:].lstrip()
        out.append(_Line(text, line.page, line.level, line.title) if text is line.text else _Line(text, line.page))
    return out


# --------------------------------------------------------------------------------------------------------------- labelling


@dataclass
class _Draft:
    lines: list[_Line]

    @property
    def tokens(self) -> int:
        return tokens(_join(self.lines))


def _merge_small(chunks: list[list[_Line]]) -> list[list[_Line]]:
    drafts: list[_Draft] = []
    for chunk in chunks:
        draft = _Draft(chunk)
        if drafts and (draft.tokens < MIN_TOKENS or drafts[-1].tokens < MIN_TOKENS) and drafts[-1].tokens + draft.tokens <= TARGET_MAX_TOKENS:
            drafts[-1].lines.extend(chunk)
        else:
            drafts.append(draft)
    return [d.lines for d in drafts]


def _opens_with_heading(chunk: list[_Line]) -> _Line | None:
    for line in chunk:
        if line.text.strip() and not _PAGE.match(line.text):
            return line if line.level else None
    return None


def _label(chunks: list[list[_Line]], document_title: str, *, by_pages: bool = False) -> list[Section]:
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    for chunk in chunks:
        opening = _opens_with_heading(chunk)
        opened_path: tuple[str, ...] | None = None
        start_path = tuple(title for _level, title in path)
        for line in chunk:
            if line.level:
                while path and path[-1][0] >= line.level:
                    path.pop()
                path.append((line.level, line.title))
                if line is opening:
                    opened_path = tuple(title for _level, title in path)
        heading_path = opened_path if opened_path is not None else start_path
        pages = [line.page for line in chunk if line.text.strip()]
        if by_pages:
            first, last = (min(pages), max(pages)) if pages else (1, 1)
            title = f"Page {first}" if first == last else f"Pages {first}–{last}"
        else:
            title = opening.title if opening else (heading_path[-1] if heading_path else document_title)
        seen[title] = seen.get(title, 0) + 1
        sections.append(
            Section(
                title=title if seen[title] == 1 else f"{title} (part {seen[title]})",
                heading_path=heading_path or (document_title,),
                first_page=min(pages) if pages else 1,
                last_page=max(pages) if pages else 1,
                markdown=_join(chunk),
            )
        )
    return sections


__all__ = ["HARD_MAX_TOKENS", "MIN_TOKENS", "TARGET_MAX_TOKENS", "Section", "split", "tokens"]
