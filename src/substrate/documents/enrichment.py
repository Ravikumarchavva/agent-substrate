"""Enrichment — model-written descriptions and topic filing for a ``Library``'s documents.

``Library.add`` builds a bundle with plain code, so its index says only what can be counted ("3 pages · 1 sections"). That cannot guide a
model through thousands of documents. An ``Enricher`` reads a document's sections and writes what a person (or the assistant) needs in order to
choose what to open: a sentence or two per section, a short card for the document, and where it belongs in a topic tree.

Rules the rest of the library relies on:

* Enrichment is **additive**. It never changes a section's text, never moves a document, never deletes anything; a document without it is
  exactly as usable as before, with the counted index.
* It is **labelled**. Everything written carries ``generated`` (who, when) and no ``verified``, so it reads as Unverified.
* It is **distrusted twice**. The text a model produced from an untrusted document is cleaned (no links, markup or instructions
  carried through) and every figure in it is checked against the document before it is stored.
* It **fails soft**. A model that is down, slow or wrong leaves the document with its counted index and a state of ``failed``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, runtime_checkable

MIN_ENRICH_TOKENS = 1000
"""A document shorter than this is its own summary; it is not sent to a model."""

SECTION_DESCRIPTION_CHARS = 280
CARD_CHARS = 600
TOPIC_DEPTH = 3
TOPIC_SEGMENT_CHARS = 40


@dataclass(frozen=True)
class SectionBrief:
    """One section as an ``Enricher`` sees it."""

    position: int
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    tokens: int
    text: str


@dataclass(frozen=True)
class DocumentBrief:
    """A document as an ``Enricher`` sees it: its facts and its sections' text. Untrusted: it is whatever the uploader wrote."""

    document: str
    title: str
    filename: str
    pages: int
    sections: tuple[SectionBrief, ...]


@dataclass(frozen=True)
class EnrichmentUsage:
    """What an enrichment cost, for the tenant's ledger and for telemetry."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


@dataclass(frozen=True)
class Described:
    """What an ``Enricher`` returns: a description per section position, a card for the document, and up to two topic paths."""

    sections: Mapping[int, str]
    card: str
    topics: tuple[str, ...] = ()
    usage: EnrichmentUsage = field(default_factory=EnrichmentUsage)


@runtime_checkable
class Enricher(Protocol):
    """Contract for whatever writes descriptions: a language model, a rule, a person's script.

    ``name`` and ``version`` identify the author in ``generated.by``; change ``version`` whenever the prompt or logic changes, so documents
    written by the old one are re-enriched. ``topics`` is the filing tree so far (slash-separated paths, bounded): prefer an existing one.
    May raise; the library records the failure and moves on.
    """

    name: str
    version: str

    async def enrich(
        self, brief: DocumentBrief, *, topics: Sequence[str]
    ) -> Described: ...


@dataclass(frozen=True)
class Enriched:
    """The outcome of ``Library.enrich``."""

    document: str
    state: str
    """``done``, ``skipped`` (too short, or nothing to do), ``failed``, or ``missing`` (no such document)."""
    card: str = ""
    sections: int = 0
    topics: tuple[str, ...] = ()
    usage: EnrichmentUsage = field(default_factory=EnrichmentUsage)
    warnings: tuple[str, ...] = ()
    error: str | None = None


# ----------------------------------------------------------------------------------------------------------------------- cleaning

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_TAG = re.compile(r"<[^>]{0,200}>")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
_BULLET = re.compile(r"^[\s>*#\-–•]+")


def clean(text: str, *, limit: int) -> str:
    """Text safe to store in an index a model will read: no links, URLs, HTML or control characters, one line, at most ``limit`` characters.

    The input came from a model that read an untrusted document, so anything that could carry an instruction or a payload through to the
    next reader is flattened to plain words."""
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _TAG.sub("", text)
    text = _CONTROL.sub(" ", text)
    text = _SPACE.sub(" ", text).strip()
    text = _BULLET.sub("", text).strip(" \"'`")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


# ---------------------------------------------------------------------------------------------------------------------- grounding

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _digits(number: str) -> str:
    """The significant digits of a figure: separators and leading/trailing zeros removed (``119,575`` → ``119575``, ``119.6`` → ``1196``)."""
    return number.replace(",", "").replace(".", "").lstrip("0").rstrip("0") or "0"


def _figures(text: str) -> set[str]:
    return {_digits(m.group(0)) for m in _NUMBER.finditer(text)}


def _supported(figure: str, source: set[str]) -> bool:
    """Whether a figure in a description appears in the source, allowing it to be a rounding of one that does (``119.6`` for ``119,575``)."""
    if figure in source:
        return True
    n = len(figure)
    for s in source:
        if len(s) <= n:
            continue
        if s[:n] == figure:  # truncated
            return True
        rounded = (Decimal(s[: n + 1]) / 10).to_integral_value(rounding=ROUND_HALF_UP)
        if str(int(rounded)) == figure:  # rounded: 1195|75 -> 1196
            return True
    return False


def ground(
    description: str, source: str, *, min_digits: int = 3
) -> tuple[str, list[str]]:
    """``description`` without the sentences that state a figure the source does not contain, and the figures that were not found.

    A figure counts as supported if it appears in ``source`` as written or as a rounding of one that does (``$119.6B`` for ``119,575``). Small
    numbers (under ``min_digits`` significant digits: counts, percentages, ordinals) are not checked, since they are usually derived. A
    description that loses every sentence is returned empty; the caller falls back to a plain extract."""
    source_figures = _figures(source)
    kept: list[str] = []
    unsupported: list[str] = []
    for sentence in sentences(description):
        bad = [
            m.group(0)
            for m in _NUMBER.finditer(sentence)
            if len(_digits(m.group(0))) >= min_digits
            and not _supported(_digits(m.group(0)), source_figures)
        ]
        if bad:
            unsupported.extend(bad)
        else:
            kept.append(sentence)
    return " ".join(kept), unsupported


# ------------------------------------------------------------------------------------------------------------------------- topics


def topic_path(value: str) -> str:
    """A topic as a clean ``A/B/C`` path: at most ``TOPIC_DEPTH`` levels, each a short plain name. ``""`` if nothing usable is left."""
    parts: list[str] = []
    for raw in value.replace("\\", "/").split("/"):
        name = _SPACE.sub(
            " ", re.sub(r"[^\w &,.'-]", " ", raw, flags=re.UNICODE)
        ).strip(" .-")
        if name:
            parts.append(name[:TOPIC_SEGMENT_CHARS].strip())
    return "/".join(parts[:TOPIC_DEPTH])


# ------------------------------------------------------------------------------------------------------------------ the plain enricher


class FirstSentenceEnricher:
    """An ``Enricher`` with no model: a section is described by its first sentences, the card by the first section's. Free, deterministic and
    offline, so it is the test double, the fallback when a model is unavailable, and the proof that the pipeline works without one."""

    name = "first-sentence"
    version = "1"

    async def enrich(self, brief: DocumentBrief, *, topics: Sequence[str]) -> Described:
        sections = {
            s.position: extract(s.text, limit=SECTION_DESCRIPTION_CHARS)
            for s in brief.sections
        }
        first = next((d for d in sections.values() if d), "")
        return Described(sections=sections, card=clean(first, limit=CARD_CHARS))


def extract(markdown: str, *, limit: int) -> str:
    """The opening sentences of a section's prose, without headings, page markers, tables or figure links."""
    prose: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith(("#", "<!--", "|", "![", "```", "---"))
            or set(stripped) <= set("|-: ")
        ):
            continue
        prose.append(stripped)
        if sum(len(p) for p in prose) > limit * 2:
            break
    return clean(" ".join(prose), limit=limit)


__all__ = [
    "CARD_CHARS",
    "MIN_ENRICH_TOKENS",
    "SECTION_DESCRIPTION_CHARS",
    "TOPIC_DEPTH",
    "Described",
    "DocumentBrief",
    "Enriched",
    "Enricher",
    "EnrichmentUsage",
    "FirstSentenceEnricher",
    "SectionBrief",
    "clean",
    "extract",
    "ground",
    "sentences",
    "topic_path",
]
