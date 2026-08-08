"""Tests for the grounded-citation layer (capabilities/knowledge/citations.py)."""

from __future__ import annotations

from substrate.capabilities.knowledge.citations import (
    CitationLedger,
    CitationLedgerStore,
    build_citations,
)
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import SearchResult


def _result(
    text: str = "passage text",
    *,
    score: float = 0.9,
    filename: str | None = "Naac_appLetter.pdf",
    file_id: str = "file-1",
    session_path: str | None = None,
    page_number: int | None = None,
    pages: list[int] | None = None,
) -> SearchResult:
    metadata: dict[str, object] = {}
    if filename is not None:
        metadata["filename"] = filename
    if file_id:
        metadata["file_id"] = file_id
    if session_path is not None:
        metadata["session_path"] = session_path
    if page_number is not None:
        metadata["page_number"] = page_number
    if pages is not None:
        metadata["pages"] = pages
    return SearchResult(
        id="r1", content=[TextBlock(text=text)], score=score, metadata=metadata
    )


def _cite(results, ledger=None, *, collection="thread-1", backend="pinecone"):
    return build_citations(
        results,
        backend_name=backend,
        collection=collection,
        ledger=ledger or CitationLedger(),
    )


# ── Dedup + numbering ───────────────────────────────────────────────────────


def test_same_file_and_page_collapse_to_one_citation():
    cited = _cite([_result(page_number=5), _result("another chunk", page_number=5)])

    assert len(cited.citations) == 1
    assert cited.index_for == [1, 1]


def test_different_pages_get_different_indices():
    cited = _cite([_result(page_number=5), _result(page_number=6)])

    assert [c.index for c in cited.citations] == [1, 2]
    assert cited.index_for == [1, 2]


def test_index_is_stable_across_calls_with_the_same_ledger():
    """The prompt asks for 2-3 knowledge_search calls per turn; a passage must
    keep its number across them or the model's [n] means different things."""
    ledger = CitationLedger()

    first = _cite([_result(page_number=5)], ledger)
    second = _cite([_result(page_number=7), _result(page_number=5)], ledger)

    assert first.index_for == [1]
    # p.7 is new (2); p.5 reuses its original number (1).
    assert second.index_for == [2, 1]


def test_every_call_emits_the_cumulative_citation_list():
    """The frontend merges by index and must be able to rebuild the full list
    from the last event alone (reload path)."""
    ledger = CitationLedger()

    _cite([_result(page_number=5)], ledger)
    second = _cite([_result(page_number=7)], ledger)

    assert [c.index for c in second.citations] == [1, 2]


def test_results_are_deduped_by_file_id_not_filename():
    a = _result(filename="report.pdf", file_id="file-a", page_number=1)
    b = _result(filename="report.pdf", file_id="file-b", page_number=1)

    cited = _cite([a, b])

    assert cited.index_for == [1, 2]


# ── Pages / labels ──────────────────────────────────────────────────────────


def test_coarse_multipage_chunk_jumps_to_first_page_and_shows_the_range():
    """Pinecone's multimodal chunker can merge a whole doc into one chunk."""
    cited = _cite([_result(pages=[1, 2, 3, 4, 5, 6, 7])])
    citation = cited.citations[0]

    assert citation.page == 1
    assert citation.pages == (1, 2, 3, 4, 5, 6, 7)
    assert citation.label() == "(Naac_appLetter.pdf, pp.1-7)"


def test_single_page_label():
    assert _cite([_result(page_number=5)]).citations[0].label() == (
        "(Naac_appLetter.pdf, p.5)"
    )


def test_non_contiguous_pages_label():
    cited = _cite([_result(pages=[9, 1, 4])])

    assert cited.citations[0].label() == "(Naac_appLetter.pdf, pp.1,4,9)"


def test_missing_page_yields_none_and_a_filename_only_label():
    """DOCX/PPTX go through docling as one whole-file Document — no page."""
    cited = _cite([_result(filename="report.docx", page_number=None)])
    citation = cited.citations[0]

    assert citation.page is None
    assert citation.pages == ()
    assert citation.label() == "(report.docx)"


def test_unparseable_page_values_are_ignored():
    result = SearchResult(
        id="r1",
        content=[TextBlock(text="x")],
        score=0.5,
        metadata={"filename": "a.pdf", "pages": ["not-a-number", None]},
    )

    assert _cite([result]).citations[0].page is None


# ── Grounding guarantees ────────────────────────────────────────────────────


def test_result_without_filename_gets_no_citation():
    """Nothing servable to link to → no index → the model has no number to
    cite. A chip must never exist without a real source behind it."""
    cited = _cite([_result(filename=None)])

    assert cited.citations == []
    assert cited.index_for == [0]


def test_uncitable_and_citable_results_mix_correctly():
    cited = _cite([_result(filename=None), _result(page_number=3)])

    assert cited.index_for == [0, 1]
    assert len(cited.citations) == 1


# ── session_path (the UI's file URL) ────────────────────────────────────────


def test_session_path_is_used_when_present():
    cited = _cite([_result(session_path="Naac_appLetter-1.pdf", page_number=1)])

    assert cited.citations[0].session_path == "Naac_appLetter-1.pdf"


def test_session_path_falls_back_to_filename():
    """Files ingested before session_path was added still open, as long as the
    upload's object key wasn't uniquified."""
    cited = _cite([_result(page_number=1)])

    assert cited.citations[0].session_path == "Naac_appLetter.pdf"


# ── Wire shape ──────────────────────────────────────────────────────────────


def test_to_wire_shape_is_json_native_snake_case():
    cited = _cite([_result("  a   long   passage  ", page_number=5)])
    wire = cited.to_wire()

    assert list(wire) == ["citations"]
    entry = wire["citations"][0]
    assert entry["index"] == 1
    assert entry["file_name"] == "Naac_appLetter.pdf"
    assert entry["page"] == 5
    assert entry["pages"] == [5]
    assert entry["thread_id"] == "thread-1"
    assert entry["backend"] == "pinecone"
    # Whitespace collapsed for a clean hover preview.
    assert entry["snippet"] == "a long passage"


def test_snippet_is_truncated():
    cited = _cite([_result("x" * 5000, page_number=1)])

    assert len(cited.citations[0].snippet) == 240


def test_empty_results_produce_nothing():
    cited = _cite([])

    assert cited.citations == []
    assert cited.index_for == []
    assert cited.to_wire() == {"citations": []}


# ── Ledger store ────────────────────────────────────────────────────────────


def test_ledger_store_returns_the_same_ledger_per_collection():
    store = CitationLedgerStore()

    assert store.get("thread-a") is store.get("thread-a")
    assert store.get("thread-a") is not store.get("thread-b")


def test_ledger_store_evicts_least_recently_used():
    store = CitationLedgerStore(max_collections=2)
    first = store.get("a")
    store.get("b")
    store.get("a")  # refresh "a" so "b" is now the LRU
    store.get("c")  # evicts "b"

    assert store.get("a") is first
    assert store.get("b") is not first
