"""Citations — turn retrieval results into grounded, clickable source references.

A ``Citation`` is built *only* from metadata a backend actually returned for a
retrieved passage. Nothing here infers, guesses, or accepts a model-authored
source: if a result carries no ``filename`` it gets no citation index at all, so
the model has no number it could cite. That's what makes the resulting source
list trustworthy in the UI.

Numbering is owned by a ``CitationLedger`` rather than by each call, because the
chat prompt asks the model to issue several ``knowledge_search`` calls per turn.
If every call numbered its passages from 1, the model's ``[2]`` would mean a
different passage depending on which call it came from — plausible-looking but
wrong provenance. A ledger keyed on ``(file, page)`` hands the same passage the
same number for the life of a collection.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Any

from substrate.kernel.storage.vector import SearchResult

# How much of a passage travels to the UI as hover/preview text. Long enough to
# recognise the quote, short enough that a dozen citations don't bloat the SSE
# payload (the full passage already went to the model in the tool's text output).
_SNIPPET_CHARS = 240

# Matches config.RAG_MIN_RERANK_SCORE's value. Kept as a plain default here
# rather than importing substrate.config — this module has no config
# dependency today and shouldn't be the first to add one; callers that care
# about the real configured value pass it explicitly.
_DEFAULT_MIN_SCORE = 0.1

# "Near-duplicate" for overlap-window chunks: literal text overlap, not
# semantic similarity. difflib's ratio is good enough for that and needs no
# new dependency.
_DEFAULT_DEDUP_SIMILARITY = 0.9

# Characters a chunk boundary can end/start with and still count as "ends a
# sentence cleanly" — used by the mid-sentence-boundary heuristic below.
_TERMINAL_END_CHARS = (".", "!", "?", '"', "'", ")", "]", "”", "’")


@dataclass(frozen=True, slots=True)
class Citation:
    """One numbered source reference behind a retrieved passage.

    ``page`` is the jump target for the UI; ``pages`` is everything the chunk
    spanned. They differ when a backend chunks coarsely — Pinecone's multimodal
    parser can merge a whole document into one chunk, giving ``pages=(1..7)``
    and ``page=1``. ``label()`` surfaces the full range so a wide span reads as
    imprecise rather than as a confident pointer at page 1.
    """

    index: int
    file_name: str
    file_id: str = ""
    session_path: str = ""
    thread_id: str = ""
    page: int | None = None
    pages: tuple[int, ...] = ()
    score: float = 0.0
    snippet: str = ""
    backend: str = ""
    # Adjacent-chunk text attached only when a continuity signal holds (see
    # attach_adjacency_context()). Kept separate from `snippet`/the passage
    # text sent to the model — never silently concatenated — so a caller can
    # render "...continues from previous page: ..." as clearly-labelled
    # surrounding context rather than passing it off as the matched passage.
    preceding_context: str = ""
    following_context: str = ""

    def label(self) -> str:
        """Human-readable source tag, e.g. ``(report.pdf, p.5)``.

        Appended to each passage in the tool's text output so the model can
        cite ``[1]`` and mention the page in prose without inventing either.
        """
        pages = _pages_label(self.pages)
        return f"({self.file_name}, {pages})" if pages else f"({self.file_name})"

    def to_wire(self) -> dict[str, Any]:
        """Serialise for ``ToolExecutionResult.structured_content``.

        snake_case keys, JSON-native types only — this crosses the SSE
        protocol boundary and gets re-parsed by the frontend.
        """
        return {
            "index": self.index,
            "file_name": self.file_name,
            "file_id": self.file_id,
            "session_path": self.session_path,
            "thread_id": self.thread_id,
            "page": self.page,
            "pages": list(self.pages),
            "score": round(self.score, 4),
            "snippet": self.snippet,
            "backend": self.backend,
            "preceding_context": self.preceding_context,
            "following_context": self.following_context,
        }


def _pages_label(pages: tuple[int, ...]) -> str:
    """``p.5`` for one page, ``pp.1-7`` for a contiguous run, else ``pp.1,4,9``."""
    if not pages:
        return ""
    if len(pages) == 1:
        return f"p.{pages[0]}"
    ordered = sorted(pages)
    contiguous = ordered[-1] - ordered[0] == len(ordered) - 1
    if contiguous:
        return f"pp.{ordered[0]}-{ordered[-1]}"
    return "pp." + ",".join(str(p) for p in ordered)


def _pages_of(metadata: dict[str, Any]) -> tuple[int, ...]:
    """Normalise the two shapes backends produce into one tuple.

    Pinecone reports a whole chunk's span as ``pages``; the local pypdf/
    pdfplumber path indexes one ``Document`` per page and reports a single
    ``page_number``. Coercing here keeps that difference out of everything
    downstream.
    """
    raw = metadata.get("pages")
    if isinstance(raw, (list, tuple)) and raw:
        pages: list[int] = []
        for value in raw:
            try:
                pages.append(int(value))
            except (TypeError, ValueError):
                continue
        if pages:
            return tuple(sorted(pages))
    single = metadata.get("page_number")
    try:
        return (int(single),) if single is not None else ()
    except (TypeError, ValueError):
        return ()


def _snippet_of(result: SearchResult) -> str:
    return " ".join(result.to_text().split())[:_SNIPPET_CHARS]


# ── Score threshold + near-duplicate suppression ────────────────────────────
#
# Both run as a pre-pass inside build_citations(), in this order: score
# threshold first (cheap, and there's no reason to run dedup comparisons over
# results that are getting dropped anyway), then near-duplicate suppression
# over the survivors. Filtered-out results are *not* removed from the
# result list — build_citations() still walks every result positionally
# (index_for/first_seen must stay aligned with the caller's `results` list,
# since callers like knowledge_search.py zip them together) — they're just
# routed down the same "uncitable" path already used for results with no
# filename: index 0, first_seen False.


def filter_by_score(
    results: list[SearchResult], min_score: float = _DEFAULT_MIN_SCORE
) -> list[SearchResult]:
    """Drop results scoring below *min_score*.

    If every result is below threshold, returns an empty list — not an
    error. Telling the model "no confident match found" is the caller's
    job; this just produces the (possibly empty) citable set.
    """
    return [r for r in results if r.score >= min_score]


def suppress_near_duplicates(
    results: list[SearchResult],
    similarity_threshold: float = _DEFAULT_DEDUP_SIMILARITY,
) -> list[SearchResult]:
    """Drop results whose text is a near-duplicate of an already-kept one.

    "Near-duplicate" means ``SequenceMatcher(None, a, b).ratio()`` exceeds
    *similarity_threshold* — literal text overlap, the kind two overlap-
    window chunks of the same passage produce, not semantic similarity.
    Highest-scoring result of a duplicate cluster wins; the rest are
    dropped. Survivors are returned in their original relative order.
    """
    winners_first = sorted(range(len(results)), key=lambda i: (-results[i].score, i))
    kept_ids: set[int] = set()
    kept_texts: list[str] = []
    for i in winners_first:
        text = results[i].to_text()
        if any(
            SequenceMatcher(None, text, kept).ratio() > similarity_threshold
            for kept in kept_texts
        ):
            continue
        kept_ids.add(i)
        kept_texts.append(text)
    return [r for i, r in enumerate(results) if i in kept_ids]


# ── Continuity-gated adjacency context ──────────────────────────────────────

ChunkLookup = Callable[[str], Awaitable[SearchResult | None]]


def _looks_open_at_start(text: str) -> bool:
    """Cheap mid-sentence heuristic: starts lowercase → likely continues a
    sentence the previous chunk began."""
    stripped = text.lstrip()
    return bool(stripped) and stripped[0].islower()


def _looks_open_at_end(text: str) -> bool:
    """Cheap mid-sentence heuristic: no terminal punctuation → likely cut off
    mid-sentence, continuing into the next chunk."""
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] not in _TERMINAL_END_CHARS


def _same_section(a: dict[str, Any], b: dict[str, Any]) -> bool:
    section_a = a.get("section_id")
    section_b = b.get("section_id")
    return section_a is not None and section_a == section_b


async def attach_adjacency_context(
    citation: Citation,
    result: SearchResult,
    *,
    get_chunk_by_id: ChunkLookup,
) -> Citation:
    """Return *citation* with neighbouring-chunk text attached, gated by a
    continuity signal per side.

    ``result.metadata`` is expected to carry ``StructureAwareChunker``'s
    ``prev_chunk_id`` / ``next_chunk_id`` / ``section_id`` keys. For each
    side, the neighbour (fetched via the injected *get_chunk_by_id*) is
    attached only when a continuity signal holds for that side:

    - the neighbour shares the current chunk's ``section_id``, OR
    - the current chunk's own text looks cut off on that side (starts
      lowercase for the preceding side, has no terminal punctuation for the
      following side — see ``_looks_open_at_start``/``_looks_open_at_end``).

    Neither signal holding means no neighbour is attached on that side. A
    missing chunk id, or a lookup that returns ``None``, also leaves that
    side unattached. Never mutates *citation* or concatenates neighbour text
    into its primary fields — returns a new ``Citation`` (or the same one,
    unchanged, when nothing qualifies).
    """
    metadata = result.metadata or {}
    text = result.to_text()
    prev_id = metadata.get("prev_chunk_id")
    next_id = metadata.get("next_chunk_id")

    preceding_context = citation.preceding_context
    following_context = citation.following_context

    if prev_id:
        prev = await get_chunk_by_id(prev_id)
        if prev is not None and (
            _same_section(metadata, prev.metadata or {}) or _looks_open_at_start(text)
        ):
            preceding_context = prev.to_text()

    if next_id:
        nxt = await get_chunk_by_id(next_id)
        if nxt is not None and (
            _same_section(metadata, nxt.metadata or {}) or _looks_open_at_end(text)
        ):
            following_context = nxt.to_text()

    if (
        preceding_context == citation.preceding_context
        and following_context == citation.following_context
    ):
        return citation
    return replace(
        citation,
        preceding_context=preceding_context,
        following_context=following_context,
    )


class CitationLedger:
    """Stable ``(file, page) -> index`` assignment for one collection.

    Held per collection by the tool for the process's lifetime so numbers stay
    consistent across every ``knowledge_search`` call in a conversation. A
    restart resets it, which can renumber *future* turns — harmless, because the
    UI snapshots sources onto each message as it arrives rather than resolving
    them against live state.
    """

    __slots__ = ("_indices", "_citations")

    def __init__(self) -> None:
        self._indices: dict[tuple[str, int | None], int] = {}
        self._citations: dict[int, Citation] = {}

    def assign(
        self, key: tuple[str, int | None], build: Callable[[int], Citation]
    ) -> tuple[int, bool]:
        """Return ``(index, first_seen)`` for *key*, calling ``build(index)``
        on first sight.

        ``first_seen`` is what lets a caller tell "this passage is new to the
        conversation" from "the retriever handed me the same passage again" —
        see ``knowledge_search.py``, which uses it to avoid re-attaching an
        image it already sent.
        """
        existing = self._indices.get(key)
        if existing is not None:
            return existing, False
        index = len(self._indices) + 1
        self._indices[key] = index
        self._citations[index] = build(index)
        return index, True

    def snapshot(self) -> list[Citation]:
        """Every citation assigned so far, in index order."""
        return [self._citations[i] for i in sorted(self._citations)]


class CitationLedgerStore:
    """Bounded per-collection ledger cache.

    One ledger per chat thread, capped so a long-lived server doesn't retain
    every thread it has ever served. Evicting a ledger only renumbers that
    collection's later turns, for the same reason a restart is safe.
    """

    __slots__ = ("_ledgers", "_max_collections")

    def __init__(self, *, max_collections: int = 64) -> None:
        self._ledgers: OrderedDict[str, CitationLedger] = OrderedDict()
        self._max_collections = max_collections

    def get(self, collection: str) -> CitationLedger:
        ledger = self._ledgers.get(collection)
        if ledger is None:
            ledger = CitationLedger()
            self._ledgers[collection] = ledger
            if len(self._ledgers) > self._max_collections:
                self._ledgers.popitem(last=False)
        else:
            self._ledgers.move_to_end(collection)
        return ledger


@dataclass(frozen=True, slots=True)
class CitationSet:
    """Result of citing one batch of retrieval results.

    ``citations`` is the ledger's *cumulative* list for the collection, not just
    this batch — the frontend merges by index, so every emission carrying the
    full set means the last event in a thread's log is enough to rebuild the
    whole source list on reload.

    ``index_for[i]`` is the citation index for ``results[i]``, or ``0`` when that
    result couldn't be cited (no filename to point at).

    ``first_seen[i]`` is ``True`` only when ``results[i]`` introduced a passage
    the ledger had never numbered before — ``False`` when an earlier call in the
    same conversation (or an earlier result in this same batch) already covered
    it. Uncitable results are ``False``: with no ledger key there's no way to
    know whether they repeat, and treating them as new would re-attach the same
    unattributable image every call.
    """

    citations: list[Citation] = field(default_factory=list)
    index_for: list[int] = field(default_factory=list)
    first_seen: list[bool] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {"citations": [c.to_wire() for c in self.citations]}


def build_citations(
    results: list[SearchResult],
    *,
    backend_name: str,
    collection: str,
    ledger: CitationLedger,
    min_score: float = _DEFAULT_MIN_SCORE,
    dedup_similarity_threshold: float | None = None,
) -> CitationSet:
    """Assign stable citation indices to *results*.

    Deduplicates on ``(file, page)`` before numbering, so two chunks retrieved
    from the same page of the same file share one index — the model citing it
    twice is then correct rather than contradictory.

    Before that, up to two more passes run over *results* (see
    ``filter_by_score``/``suppress_near_duplicates`` docstrings for the exact
    semantics): results scoring below ``min_score`` are always dropped first;
    then, only when *dedup_similarity_threshold* is given, near-duplicate
    results losing to a higher-scoring match are dropped too.
    ``dedup_similarity_threshold`` defaults to ``None`` (disabled) rather than
    ``_DEFAULT_DEDUP_SIMILARITY`` — literal text-overlap dedup is blunt enough
    (it doesn't know two results come from different files/pages) that it
    should be an opt-in a caller reaches for, not silently on for everyone
    calling ``build_citations`` today. Pass ``_DEFAULT_DEDUP_SIMILARITY``
    (0.9) to enable it with the documented default.

    Either way, dropped results are routed down the same "uncitable" path as
    a result with no filename — index 0, ``first_seen`` False — rather than
    removed from ``results``, so ``index_for``/``first_seen`` stay
    positionally aligned with the input list for callers that zip them
    together.
    """
    citable_results = filter_by_score(results, min_score)
    if dedup_similarity_threshold is not None:
        citable_results = suppress_near_duplicates(
            citable_results, dedup_similarity_threshold
        )
    citable_ids = {id(r) for r in citable_results}

    index_for: list[int] = []
    first_seen: list[bool] = []
    for result in results:
        metadata = result.metadata or {}
        file_name = str(metadata.get("filename") or "").strip()
        if not file_name or id(result) not in citable_ids:
            # Nothing servable to link to (no filename), or filtered out by
            # score/dedup above. No index means the passage is labelled as
            # uncitable and the model has no number to cite — preferable to
            # a chip that opens nothing.
            index_for.append(0)
            first_seen.append(False)
            continue

        file_id = str(metadata.get("file_id") or "")
        pages = _pages_of(metadata)
        page = pages[0] if pages else None
        key = (file_id or file_name, page)

        # Safe to close over the loop variables: assign() calls this
        # synchronously, before the next iteration rebinds them.
        def build(index: int) -> Citation:
            return Citation(
                index=index,
                file_name=file_name,
                file_id=file_id,
                # session_path is what the UI turns into a file URL. Falls back
                # to the filename, which is right unless the upload's object key
                # was uniquified (see routes/files.py::_unique_object_key).
                session_path=str(metadata.get("session_path") or file_name),
                thread_id=collection,
                page=page,
                pages=pages,
                score=float(result.score),
                snippet=_snippet_of(result),
                backend=backend_name,
            )

        index, is_new = ledger.assign(key, build)
        index_for.append(index)
        first_seen.append(is_new)

    return CitationSet(
        citations=ledger.snapshot(), index_for=index_for, first_seen=first_seen
    )


__all__ = [
    "ChunkLookup",
    "Citation",
    "CitationLedger",
    "CitationLedgerStore",
    "CitationSet",
    "attach_adjacency_context",
    "build_citations",
    "filter_by_score",
    "suppress_near_duplicates",
]
