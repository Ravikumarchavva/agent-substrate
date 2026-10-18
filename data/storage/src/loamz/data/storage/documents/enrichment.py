"""Enrichment — model-written descriptions and topic filing for a document library's documents.

``add`` builds a bundle with plain code, so its index says only what can be counted ("3 pages · 1 sections"). That cannot guide a
model through thousands of documents. An ``Enricher`` reads a document's sections and writes what a person (or the assistant) needs in order to
choose what to open: a sentence or two per section, a short card for the document, and where it belongs in a topic tree.

Rules the rest of the library relies on:

* Enrichment is **additive**. It never changes a section's text, never moves a document, never deletes anything; a document without it is
  exactly as usable as before, with the counted index.
* It is **labelled**. Everything written carries ``generated`` (who, when) and no ``verified``, so it reads as Unverified.
* It is **distrusted twice**. The text a model produced from an untrusted document is cleaned (no links, markup or instructions
  carried through) and every figure in it is checked against the document before it is stored.
* It **fails soft**. A model that is down, slow or wrong leaves the document with its counted index and a state of ``failed``.

``enrich`` and ``reorganise`` are free functions over a ``DocumentLibrary``: they decide *what* to write (the claim, the idempotency rule, the
figure check, the retry bookkeeping) and hand it to the library's small primitives (``claim``, ``annotate``, ``set_topics``, ``fail``). The
model, its timeout and its token budget belong to the caller, so the same code runs over the folder ``Library`` and over a remote one.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import weakref
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from loamz.data.storage.documents.types import (
    Described,
    DocumentBrief,
    Enriched,
    Enricher,
    EnrichmentUsage,
    FiledDocument,
    Organised,
    Organiser,
    Reorganised,
    SectionBrief,
)

if TYPE_CHECKING:
    from loamz.data.storage.documents.protocols import DocumentLibrary

logger = logging.getLogger(__name__)

STALE_RUNNING_S = 600
"""A document marked ``running`` for longer than this was abandoned (the process died) and is described again."""

MIN_ENRICH_TOKENS = 1000
"""A document shorter than this is its own summary; it is not sent to a model."""

SECTION_DESCRIPTION_CHARS = 280
CARD_CHARS = 600
TOPIC_DEPTH = 3
TOPIC_SEGMENT_CHARS = 40
MAX_TOPIC_CHILDREN = 12
"""Past this many subtopics under one topic (or twice as many documents filed straight into one) the tree is hard to browse; a whole-tree
reorganisation is due."""


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


def ground(description: str, source: str, *, min_digits: int = 3) -> tuple[str, list[str]]:
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
    value = _TAG.sub(
        "", _URL.sub("", _LINK.sub(r"\1", value))
    )  # a link or tag is flattened before its slashes are read as levels
    for raw in value.replace("\\", "/").split("/"):
        name = _SPACE.sub(" ", re.sub(r"[^\w &,.'-]", " ", raw, flags=re.UNICODE)).strip(" .-")
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
            s.position: extract(s.text, limit=SECTION_DESCRIPTION_CHARS) for s in brief.sections
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


# ---------------------------------------------------------------------------------------------------------------- over a DocumentLibrary

_LOCKS: weakref.WeakKeyDictionary[Any, dict[tuple[str, ...], asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _lock(library: Any, *key: str) -> asyncio.Lock:
    """One lock per library and key: a document is described by one caller at a time in this process (a second waits, then finds it done
    and does not pay for the model again)."""
    try:
        locks = _LOCKS.setdefault(library, {})
    except TypeError:  # a library that cannot be weakly referenced shares one table
        locks = _LOCKS.setdefault(_NO_OWNER, {})
    return locks.setdefault(key, asyncio.Lock())


class _NoOwner:
    pass


_NO_OWNER = _NoOwner()


def validate(brief: DocumentBrief, described: Described) -> tuple[dict[int, str], str, list[str]]:
    """What an enricher returned, made safe to store: cleaned, every figure checked against the text it describes, and a plain extract
    standing in wherever nothing trustworthy is left. Returns the section descriptions, the card and what was dropped."""
    warnings: list[str] = []
    by_section: dict[int, str] = {}
    for section in brief.sections:
        text = clean(
            described.sections.get(section.position, ""),
            limit=SECTION_DESCRIPTION_CHARS,
        )
        text, bad = ground(text, section.text)
        if bad:
            warnings.append(
                f"section {section.position}: dropped a sentence with figures not in the text ({', '.join(bad[:3])})"
            )
        by_section[section.position] = text or extract(
            section.text, limit=SECTION_DESCRIPTION_CHARS
        )
    card = clean(described.card, limit=CARD_CHARS)
    card, bad = ground(card, "\n".join(s.text for s in brief.sections))
    if bad:
        warnings.append(
            f"card: dropped a sentence with figures not in the document ({', '.join(bad[:3])})"
        )
    card = card or next((d for d in by_section.values() if d), "") or brief.title
    return by_section, card, warnings


async def enrich(
    library: DocumentLibrary,
    *,
    collection: str,
    document: str,
    enricher: Enricher | None,
    force: bool = False,
    timeout: float = 180.0,
) -> Enriched:
    """Have ``enricher`` write a card for ``document`` and a description for each section, and file it under a topic when the library files
    topics.

    Safe to call again: a document already described by the same enricher version is left alone unless ``force``. It never raises for a
    model that is down, slow or wrong; it records ``failed`` and leaves the counted index in place. A document too short to need a model is
    described by its own first sentences, for free. Nothing the document says about itself is altered: section text stays byte for byte.
    Only one caller at a time describes a given document in this process."""
    collection = collection.strip("/")
    async with _lock(library, "enrich", collection, document):
        return await _enrich(
            library,
            collection=collection,
            document=document,
            enricher=enricher,
            force=force,
            timeout=timeout,
        )


async def _enrich(
    library: DocumentLibrary,
    *,
    collection: str,
    document: str,
    enricher: Enricher | None,
    force: bool,
    timeout: float,
) -> Enriched:
    if enricher is None:
        return Enriched(document, "skipped", error="no enricher is configured")
    info = await library.info(collection, document)
    if info is None:
        return Enriched(document, "missing")
    state = await library.enrichment_state(collection=collection, document=document)
    if state is not None and not force:
        if (
            state.state == "done"
            and state.enricher == enricher.name
            and state.version == enricher.version
        ):
            return Enriched(
                document,
                "done",
                card=info.description or "",
                sections=info.sections,
                topics=info.topics,
            )
        if state.state == "running" and time.time() - state.updated_at < STALE_RUNNING_S:
            return Enriched(document, "skipped", error="it is already being described")

    brief = await library.brief(collection=collection, document=document)
    if brief is None:
        return Enriched(document, "missing")
    used: Enricher = (
        FirstSentenceEnricher()
        if sum(s.tokens for s in brief.sections) < MIN_ENRICH_TOKENS
        else enricher
    )
    attempts = state.attempts + 1 if state else 1
    await library.claim(collection=collection, document=document, enricher=used, attempts=attempts)
    tree = await library.topic_tree(collection=collection) if library.file_topics else []
    try:
        described = await asyncio.wait_for(used.enrich(brief, topics=tree), timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — a model's failure must never reach the upload path
        logger.warning("enrichment of %s/%s by %s failed: %s", collection, document, used.name, exc)
        reason = (
            "the description took too long"
            if isinstance(exc, TimeoutError)
            else "the description could not be written"
        )
        await library.fail(
            collection=collection,
            document=document,
            enricher=used,
            attempts=attempts,
            error=reason,
        )
        return Enriched(document, "failed", error=reason)

    by_section, card, warnings = validate(brief, described)
    topics = (
        [t for t in dict.fromkeys(topic_path(x) for x in described.topics) if t][:2]
        if library.file_topics
        else []
    )
    try:
        await library.annotate(
            collection=collection,
            document=document,
            card=card,
            sections=by_section,
            topics=topics,
            enricher=used,
            attempts=attempts,
            usage=described.usage,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not store the description of %s/%s: %s", collection, document, exc)
        reason = "the description could not be saved"
        await library.fail(
            collection=collection,
            document=document,
            enricher=used,
            attempts=attempts,
            error=reason,
        )
        return Enriched(document, "failed", error=reason)
    return Enriched(
        document,
        "done",
        card=card,
        sections=len(by_section),
        topics=tuple(topics),
        usage=described.usage,
        warnings=tuple(warnings),
    )


async def reorganise(
    library: DocumentLibrary,
    *,
    collection: str,
    organiser: Organiser | None,
    timeout: float = 180.0,
) -> Reorganised:
    """Redraw the topic tree of ``collection`` in one pass: an ``Organiser`` sees every described document's card and the topics it has, and
    says where each should be filed. Filing documents one at a time drifts; seeing them all at once is more coherent.

    Documents never move and nothing is deleted: only each document's topics and the topic pages change, and the previous topics are noted in
    the topic log. Never raises for a model that is down or wrong; the tree stays as it was and the outcome says ``failed``."""
    collection = collection.strip("/")
    if not library.file_topics or organiser is None:
        return Reorganised("skipped", error="topic filing is not on")
    async with _lock(library, "reorganise", collection):
        return await _reorganise(
            library, collection=collection, organiser=organiser, timeout=timeout
        )


async def _described(library: DocumentLibrary, collection: str) -> list[Any]:
    """The documents of ``collection`` that have a model-written card (all of them, oldest first)."""
    out: list[Any] = []
    after: str | None = None
    while True:
        page = await library.list(collection=collection, limit=50, after=after)
        out += [d for d in page.documents if d.description]
        if page.next is None:
            return out
        after = page.next


async def _reorganise(
    library: DocumentLibrary,
    *,
    collection: str,
    organiser: Organiser,
    timeout: float,
) -> Reorganised:
    listing = await _described(library, collection)
    if len(listing) < 2:
        return Reorganised("skipped", error="too few described documents")
    before = {d.document: d.topics for d in listing}
    try:
        organised = await asyncio.wait_for(
            organiser.organise(
                [FiledDocument(d.document, d.title, d.description or "", d.topics) for d in listing]
            ),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — a model's failure must leave the tree as it was
        logger.warning("reorganising %s by %s failed: %s", collection, organiser.name, exc)
        return Reorganised("failed", error="the topics could not be redrawn")
    plan: dict[str, tuple[str, ...]] = {}
    for document, proposed in organised.topics.items():
        if document not in before:
            continue  # a document the model made up
        cleaned = tuple(dict.fromkeys(t for t in (topic_path(x) for x in proposed) if t))[:2]
        if cleaned and cleaned != before[document]:
            plan[document] = cleaned
    if not plan:
        return Reorganised(
            "done",
            moved=0,
            topics=len({t for ts in before.values() for t in ts}),
            usage=organised.usage,
        )
    try:
        await library.set_topics(
            collection=collection,
            plan=plan,
            by=f"{organiser.name} {organiser.version}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not store the new topics of %s: %s", collection, exc)
        return Reorganised("failed", error="the new topics could not be saved")
    after = {**before, **plan}
    return Reorganised(
        "done",
        moved=len(plan),
        topics=len({t for ts in after.values() for t in ts}),
        usage=organised.usage,
    )


__all__ = [
    "CARD_CHARS",
    "MAX_TOPIC_CHILDREN",
    "MIN_ENRICH_TOKENS",
    "SECTION_DESCRIPTION_CHARS",
    "STALE_RUNNING_S",
    "TOPIC_DEPTH",
    "Described",
    "DocumentBrief",
    "Enriched",
    "Enricher",
    "EnrichmentUsage",
    "FiledDocument",
    "FirstSentenceEnricher",
    "Organised",
    "Organiser",
    "Reorganised",
    "SectionBrief",
    "clean",
    "enrich",
    "extract",
    "ground",
    "reorganise",
    "sentences",
    "topic_path",
    "validate",
]
