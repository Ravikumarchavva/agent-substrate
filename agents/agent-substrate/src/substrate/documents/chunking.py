"""Chunks for retrieval: a section's markdown cut into pieces an embedder can take, each knowing its page and its heading.

A chunk is whole blocks of the section packed up to ``max_tokens`` — paragraphs, list items, a table, a fenced code block are never cut in
the middle; a block over the limit is split at sentences (a table at its rows, with the header repeated), and only a sentence with no
break in it is cut at a space. The page of a chunk comes from the ``<!-- page N -->`` markers, which are dropped from its text. What is embedded
is ``embedding_text``: the chunk prefixed with its document title and heading path, because "the deadline is 30 June" means little without
knowing what it is the deadline for.

Sizes are in tokens estimated as UTF-8 bytes ÷ 3 (an over-estimate for English, the safe side of a model's input limit).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from substrate.documents.split import tokens

DEFAULT_MAX_TOKENS = 400
_MARGIN = 64
"""Left free of a model's input limit for the heading prefix and the model's own special tokens."""

_PAGE = re.compile(r"^<!-- page (\d+) -->\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+(?=\S)")


def chunk_size_for(max_input_tokens: int | None) -> int:
    """The chunk size to use for an embedder that takes ``max_input_tokens``: 400, or less if the model takes less."""
    if not max_input_tokens:
        return DEFAULT_MAX_TOKENS
    return max(64, min(DEFAULT_MAX_TOKENS, max_input_tokens - _MARGIN))


@dataclass(frozen=True)
class Chunk:
    text: str
    first_page: int
    last_page: int
    heading: str

    def embedding_text(self, title: str, heading_path: Sequence[str]) -> str:
        trail = " › ".join(part for part in (title, *heading_path) if part)
        return f"{trail}\n\n{self.text}" if trail else self.text


@dataclass
class _Block:
    text: str
    page: int
    table: bool = False


def _blocks(markdown: str) -> list[_Block]:
    out: list[_Block] = []
    page, current, fenced, kind = 1, [], False, ""

    def flush() -> None:
        nonlocal current, kind
        if current:
            out.append(_Block("\n".join(current).strip("\n"), page, table=kind == "table"))
        current, kind = [], ""

    for line in markdown.splitlines():
        marker = _PAGE.match(line)
        if marker and not fenced:
            flush()
            page = int(marker.group(1))
            continue
        if _FENCE.match(line):
            if not fenced:
                flush()
            fenced = not fenced
            current.append(line)
            if not fenced:
                flush()
            continue
        if fenced:
            current.append(line)
            continue
        if not line.strip():
            flush()
            continue
        is_row = bool(_TABLE_ROW.match(line))
        if current and ((kind == "table") != is_row):
            flush()
        kind = "table" if is_row else kind
        current.append(line)
    flush()
    return [b for b in out if b.text.strip()]


def _split_table(block: _Block, limit: int) -> list[_Block]:
    rows = block.text.splitlines()
    head, body = rows[:2], rows[2:]
    pieces: list[_Block] = []
    current: list[str] = []
    for row in body:
        if current and tokens("\n".join([*head, *current, row])) > limit:
            pieces.append(_Block("\n".join([*head, *current]), block.page, True))
            current = []
        current.append(row)
    if current:
        pieces.append(_Block("\n".join([*head, *current]), block.page, True))
    return pieces or [block]


def _split_text(block: _Block, limit: int) -> list[_Block]:
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(block.text):
        while tokens(sentence) > limit:  # a sentence with no break in it: cut at a space
            cut = sentence.rfind(" ", 0, limit * 3)
            cut = cut if cut > 0 else limit * 3
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and tokens(f"{current} {sentence}") > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [_Block(p, block.page) for p in pieces]


def chunk_section(markdown: str, *, heading: str = "", max_tokens: int = DEFAULT_MAX_TOKENS) -> list[Chunk]:
    """``markdown`` (one section of a document) as chunks of at most about ``max_tokens``, in reading order."""
    blocks: list[_Block] = []
    for block in _blocks(markdown):
        if tokens(block.text) <= max_tokens:
            blocks.append(block)
        elif block.table and len(block.text.splitlines()) > 2:
            blocks.extend(_split_table(block, max_tokens))
        else:
            blocks.extend(_split_text(block, max_tokens))
    chunks: list[Chunk] = []
    parts: list[_Block] = []

    def emit() -> None:
        if parts:
            chunks.append(Chunk("\n\n".join(b.text for b in parts), parts[0].page, parts[-1].page, heading))
            parts.clear()

    size = 0
    for block in blocks:
        cost = tokens(block.text) + 1
        if parts and size + cost > max_tokens:
            emit()
            size = 0
        parts.append(block)
        size += cost
    emit()
    return chunks


__all__ = ["Chunk", "DEFAULT_MAX_TOKENS", "chunk_section", "chunk_size_for"]
