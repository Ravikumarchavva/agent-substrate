"""Read an uploaded file into titled sections.

The local reader handles text formats (plain text, markdown, CSV, JSON, HTML); sections follow markdown headings and are
cut at paragraph breaks when long. A "page" is about 3000 characters, so citations and page ranges still work.

TODO(integration): [data] adapter - PDF, Office and scans are read by the data service's document intelligence.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from loamz.engine.errors import InvalidRequestError

PAGE_CHARS = 3000
SECTION_CHARS = 6000
_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".html", ".htm", ".xml", ".log", ".ini", ".toml", ".py", ".js", ".ts", ".sql", ".sh",
}  # fmt: skip
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_TAGS = re.compile(r"<[^>]+>")
_SCRIPTS = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ReadSection:
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    text: str


@dataclass(frozen=True)
class ReadDocument:
    title: str
    pages: int
    sections: tuple[ReadSection, ...]
    engine: str = "text"


def readable(filename: str, content_type: str | None) -> bool:
    suffix = PurePosixPath(filename.lower()).suffix
    kind = (content_type or "").split(";")[0].strip().lower()
    return (
        suffix in _TEXT_SUFFIXES
        or kind.startswith("text/")
        or kind in {"application/json", "application/xml", "application/x-yaml"}
    )


def _plain(text: str, filename: str) -> str:
    if filename.lower().endswith((".html", ".htm")) or "<html" in text[:500].lower():
        text = _TAGS.sub(" ", _SCRIPTS.sub(" ", text))
        text = html.unescape(re.sub(r"[ \t]+", " ", text))
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _cut(text: str) -> list[str]:
    """Pieces of at most `SECTION_CHARS`, cut at paragraph breaks where possible."""
    if len(text) <= SECTION_CHARS:
        return [text]
    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        while len(paragraph) > SECTION_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:SECTION_CHARS])
            paragraph = paragraph[SECTION_CHARS:]
        if current and len(current) + len(paragraph) + 2 > SECTION_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def read_document(data: bytes, filename: str, content_type: str | None = None) -> ReadDocument:
    if not readable(filename, content_type):
        raise InvalidRequestError(
            f"{filename or 'this file'} is not a text document; only text formats can be read here."
        )
    text = _plain(data.decode("utf-8", errors="replace").replace("\r\n", "\n"), filename)
    if not text:
        raise InvalidRequestError(f"{filename or 'the document'} has no readable text.")
    marks = list(_HEADING.finditer(text))
    raw: list[tuple[str, tuple[str, ...], str, int]] = []  # title, path, body, start offset
    if not marks:
        stem = PurePosixPath(filename).stem or "Document"
        raw.append((stem, (), text, 0))
    else:
        if marks[0].start() > 0 and text[: marks[0].start()].strip():
            raw.append(("Introduction", (), text[: marks[0].start()].strip(), 0))
        stack: list[tuple[int, str]] = []
        for i, mark in enumerate(marks):
            level, title = len(mark.group(1)), mark.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            raw.append(
                (title, tuple(t for _l, t in stack), text[mark.start() : end].strip(), mark.start())
            )
    sections: list[ReadSection] = []
    for title, path, body, start in raw:
        offset = start
        for n, piece in enumerate(_cut(body)):
            first = offset // PAGE_CHARS + 1
            last = (offset + max(len(piece) - 1, 0)) // PAGE_CHARS + 1
            sections.append(
                ReadSection(
                    title if n == 0 else f"{title} (cont.)", path or (title,), first, last, piece
                )
            )
            offset += len(piece)
    first_heading = marks[0].group(2).strip() if marks and marks[0].start() == 0 else ""
    title = first_heading or PurePosixPath(filename).stem or "Document"
    return ReadDocument(title, max(1, (len(text) - 1) // PAGE_CHARS + 1), tuple(sections))
