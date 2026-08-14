"""Tests for the grounded-citation layer (capabilities/knowledge/citations.py)."""

from __future__ import annotations

from substrate.capabilities.knowledge.citations import (
    Citation,
    CitationLedger,
    CitationLedgerStore,
    attach_adjacency_context,
    build_citations,
    filter_by_score,
    suppress_near_duplicates,
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


# ── Score threshold ──────────────────────────────────────────────────────────


def test_filter_by_score_drops_results_below_threshold():
    keep = _result("keep me", score=0.5)
    drop = _result("drop me", score=0.05)

    assert filter_by_score([keep, drop], min_score=0.1) == [keep]


def test_filter_by_score_all_below_threshold_returns_empty_list():
    results = [_result(score=0.01), _result(score=0.02)]

    assert filter_by_score(results, min_score=0.1) == []


def test_build_citations_drops_result_below_default_threshold():
    """build_citations wires filter_by_score in with the default min_score
    (0.1, matching config.RAG_MIN_RERANK_SCORE) — a weak match gets no index
    but doesn't error, and doesn't shift the indices of results that do."""
    weak = _result("weak match", score=0.05, page_number=1)
    strong = _result("strong match", score=0.8, page_number=2)

    cited = _cite([weak, strong])

    assert cited.index_for == [0, 1]
    assert len(cited.citations) == 1
    assert cited.citations[0].page == 2


def test_build_citations_all_below_threshold_yields_empty_citation_list():
    results = [_result(score=0.01, page_number=1), _result(score=0.02, page_number=2)]

    cited = _cite(results)

    assert cited.citations == []
    assert cited.index_for == [0, 0]


# ── Near-duplicate suppression ───────────────────────────────────────────────


def test_suppress_near_duplicates_keeps_the_higher_scoring_result():
    weaker = _result(
        "The quick brown fox jumps over the lazy dog near the river bank.",
        score=0.4,
    )
    stronger = _result(
        "The quick brown fox jumps over the lazy dog near the river bank!",
        score=0.9,
    )

    survivors = suppress_near_duplicates([weaker, stronger])

    assert survivors == [stronger]


def test_suppress_near_duplicates_keeps_distinct_texts():
    a = _result("Completely different passage about astronomy.", score=0.5)
    b = _result("An unrelated paragraph discussing tax law.", score=0.5)

    assert suppress_near_duplicates([a, b]) == [a, b]


def test_build_citations_dedup_is_opt_in_and_higher_score_wins():
    weaker = _result(
        "Revenue grew twelve percent year over year in the reporting period.",
        score=0.3,
        page_number=1,
    )
    stronger = _result(
        "Revenue grew twelve percent year over year in the reporting period",
        score=0.95,
        page_number=2,
    )

    # Disabled by default: both get their own citation.
    default_cited = _cite([weaker, stronger])
    assert default_cited.index_for == [1, 2]

    # Opted in: the near-duplicate loses to the higher-scoring result.
    deduped = build_citations(
        [weaker, stronger],
        backend_name="pinecone",
        collection="thread-1",
        ledger=CitationLedger(),
        dedup_similarity_threshold=0.9,
    )
    assert deduped.index_for == [0, 1]
    assert len(deduped.citations) == 1
    assert deduped.citations[0].page == 2


# ── Continuity-gated adjacency context ───────────────────────────────────────


def _citation(**overrides) -> Citation:
    fields = {
        "index": 1,
        "file_name": "report.pdf",
        "page": 1,
        "pages": (1,),
    }
    fields.update(overrides)
    return Citation(**fields)


async def test_adjacency_attaches_neighbor_on_same_section():
    result = _result(
        "This chunk ends cleanly.",
        page_number=1,
    )
    result.metadata["section_id"] = 3
    result.metadata["prev_chunk_id"] = "prev-1"
    result.metadata["next_chunk_id"] = None

    neighbor = _result("Preceding neighbor text.", page_number=1)
    neighbor.metadata["section_id"] = 3

    async def get_chunk_by_id(chunk_id: str) -> SearchResult | None:
        assert chunk_id == "prev-1"
        return neighbor

    citation = await attach_adjacency_context(
        _citation(), result, get_chunk_by_id=get_chunk_by_id
    )

    assert citation.preceding_context == "Preceding neighbor text."
    assert citation.following_context == ""


async def test_adjacency_does_not_attach_neighbor_from_different_section():
    result = _result("This chunk ends cleanly.", page_number=1)
    result.metadata["section_id"] = 3
    result.metadata["prev_chunk_id"] = "prev-1"
    result.metadata["next_chunk_id"] = None

    neighbor = _result("Unrelated section text.", page_number=1)
    neighbor.metadata["section_id"] = 7

    async def get_chunk_by_id(chunk_id: str) -> SearchResult | None:
        return neighbor

    citation = await attach_adjacency_context(
        _citation(), result, get_chunk_by_id=get_chunk_by_id
    )

    assert citation.preceding_context == ""
    assert citation.following_context == ""


async def test_adjacency_mid_sentence_heuristic_attaches_across_sections():
    """A chunk that starts lowercase / ends without terminal punctuation reads
    as cut mid-sentence, so the neighbor is attached even in a different
    section."""
    result = _result("continues a sentence from the previous chunk", page_number=1)
    result.metadata["section_id"] = 1
    result.metadata["prev_chunk_id"] = "prev-1"
    result.metadata["next_chunk_id"] = "next-1"

    prev_neighbor = _result("Preceding text.", page_number=1)
    prev_neighbor.metadata["section_id"] = 99
    next_neighbor = _result("Following text.", page_number=1)
    next_neighbor.metadata["section_id"] = 99

    async def get_chunk_by_id(chunk_id: str) -> SearchResult | None:
        return prev_neighbor if chunk_id == "prev-1" else next_neighbor

    citation = await attach_adjacency_context(
        _citation(), result, get_chunk_by_id=get_chunk_by_id
    )

    # Starts lowercase -> preceding attached. Ends without terminal
    # punctuation -> following attached. Both despite the section mismatch.
    assert citation.preceding_context == "Preceding text."
    assert citation.following_context == "Following text."


async def test_adjacency_clean_sentence_boundary_and_different_section_skips_both():
    result = _result("This chunk both starts and ends cleanly.", page_number=1)
    result.metadata["section_id"] = 1
    result.metadata["prev_chunk_id"] = "prev-1"
    result.metadata["next_chunk_id"] = "next-1"

    prev_neighbor = _result("Preceding text.", page_number=1)
    prev_neighbor.metadata["section_id"] = 99
    next_neighbor = _result("Following text.", page_number=1)
    next_neighbor.metadata["section_id"] = 99

    async def get_chunk_by_id(chunk_id: str) -> SearchResult | None:
        return prev_neighbor if chunk_id == "prev-1" else next_neighbor

    citation = await attach_adjacency_context(
        _citation(), result, get_chunk_by_id=get_chunk_by_id
    )

    assert citation.preceding_context == ""
    assert citation.following_context == ""


async def test_adjacency_missing_chunk_id_leaves_context_unattached():
    result = _result("This chunk ends cleanly.", page_number=1)
    result.metadata["section_id"] = 1
    result.metadata["prev_chunk_id"] = None
    result.metadata["next_chunk_id"] = None

    async def get_chunk_by_id(chunk_id: str) -> SearchResult | None:
        raise AssertionError("should not be called when neighbor id is None")

    citation = await attach_adjacency_context(
        _citation(), result, get_chunk_by_id=get_chunk_by_id
    )

    assert citation.preceding_context == ""
    assert citation.following_context == ""


async def test_adjacency_lookup_returning_none_leaves_context_unattached():
    result = _result("continues a sentence", page_number=1)
    result.metadata["section_id"] = 1
    result.metadata["prev_chunk_id"] = "prev-1"
    result.metadata["next_chunk_id"] = None

    async def get_chunk_by_id(chunk_id: str) -> SearchResult | None:
        return None

    citation = await attach_adjacency_context(
        _citation(), result, get_chunk_by_id=get_chunk_by_id
    )

    assert citation.preceding_context == ""
