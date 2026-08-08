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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from substrate.kernel.storage.vector import SearchResult

# How much of a passage travels to the UI as hover/preview text. Long enough to
# recognise the quote, short enough that a dozen citations don't bloat the SSE
# payload (the full passage already went to the model in the tool's text output).
_SNIPPET_CHARS = 240


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
) -> CitationSet:
    """Assign stable citation indices to *results*.

    Deduplicates on ``(file, page)`` before numbering, so two chunks retrieved
    from the same page of the same file share one index — the model citing it
    twice is then correct rather than contradictory.
    """
    index_for: list[int] = []
    first_seen: list[bool] = []
    for result in results:
        metadata = result.metadata or {}
        file_name = str(metadata.get("filename") or "").strip()
        if not file_name:
            # Nothing servable to link to. No index means the passage is
            # labelled as uncitable and the model has no number to cite —
            # preferable to a chip that opens nothing.
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
    "Citation",
    "CitationLedger",
    "CitationLedgerStore",
    "CitationSet",
    "build_citations",
]
