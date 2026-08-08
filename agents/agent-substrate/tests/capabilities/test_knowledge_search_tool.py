"""Tests for KnowledgeSearchTool — thin wrapper around a RagBackend."""

from __future__ import annotations

from substrate.capabilities.knowledge.backends.base import IngestResult
from substrate.capabilities.tools.ai.knowledge_search import KnowledgeSearchTool
from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.kernel.storage.vector import SearchResult


class FakeRagBackend:
    name = "fake"

    def __init__(self) -> None:
        self.ingest_calls: list[tuple] = []
        self.query_calls: list[tuple] = []

    async def ingest(
        self, source, *, collection="default", metadata=None
    ) -> IngestResult:
        self.ingest_calls.append((source, collection, metadata))
        return IngestResult(chunks_indexed=3)

    async def query(self, question, *, collection="default", limit=5):
        self.query_calls.append((question, collection, limit))
        return [
            SearchResult(
                id="1",
                content=[TextBlock(text="relevant text")],
                score=0.75,
                metadata={},
            )
        ]

    async def query_with_context(
        self, question, *, collection="default", limit=5
    ) -> str:
        return "generated answer"


async def test_knowledge_search_tool_search_calls_backend_query():
    backend = FakeRagBackend()
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="search", text="what is X?", limit=3)

    assert backend.query_calls == [("what is X?", "default", 3)]
    assert not result.is_error
    assert "relevant text" in result.content[0].text


async def test_knowledge_search_tool_ingest_calls_backend_ingest():
    backend = FakeRagBackend()
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="ingest", text="some document text")

    assert backend.ingest_calls == [("some document text", "default", None)]
    assert not result.is_error
    assert "3 chunks" in result.content[0].text


async def test_knowledge_search_tool_requires_text():
    backend = FakeRagBackend()
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="search", text="")

    assert result.is_error


async def test_knowledge_search_tool_no_results():
    class EmptyBackend(FakeRagBackend):
        async def query(self, question, *, collection="default", limit=5):
            return []

    tool = KnowledgeSearchTool(EmptyBackend())
    result = await tool.execute(action="search", text="anything")

    assert "No matching documents" in result.content[0].text


async def test_knowledge_search_tool_unknown_action():
    tool = KnowledgeSearchTool(FakeRagBackend())
    result = await tool.execute(action="bogus", text="x")

    assert result.is_error


class CitableRagBackend(FakeRagBackend):
    """A backend whose results carry real citation metadata (filename +
    page), like LocalRagBackend/PineconeRagBackend after ingest."""

    def __init__(self, results=None) -> None:
        super().__init__()
        self._results = results

    async def query(self, question, *, collection="default", limit=5):
        self.query_calls.append((question, collection, limit))
        if self._results is not None:
            return self._results
        return [
            SearchResult(
                id="1",
                content=[TextBlock(text="the certificate was signed by Dr. Shanthi")],
                score=0.9,
                metadata={"filename": "Naac_appLetter.pdf", "page_number": 5},
            )
        ]


async def test_knowledge_search_tool_labels_passages_with_citation_number():
    tool = KnowledgeSearchTool(CitableRagBackend())

    result = await tool.execute(action="search", text="who signed it?")

    assert "[1] (Naac_appLetter.pdf, p.5)" in result.content[0].text


async def test_knowledge_search_tool_emits_structured_citations():
    tool = KnowledgeSearchTool(CitableRagBackend())

    result = await tool.execute(action="search", text="who signed it?")

    citations = result.structured_content["citations"]
    assert len(citations) == 1
    assert citations[0]["file_name"] == "Naac_appLetter.pdf"
    assert citations[0]["page"] == 5
    assert citations[0]["index"] == 1


async def test_knowledge_search_tool_citation_index_stable_across_calls():
    """The chat prompt asks the model to issue several search calls per
    turn — a passage must keep its number across them."""
    results_a = [
        SearchResult(
            id="1",
            content=[TextBlock(text="page 5 content")],
            score=0.9,
            metadata={"filename": "doc.pdf", "page_number": 5},
        )
    ]
    results_b = [
        SearchResult(
            id="2",
            content=[TextBlock(text="page 7 content")],
            score=0.8,
            metadata={"filename": "doc.pdf", "page_number": 7},
        ),
        SearchResult(
            id="1",
            content=[TextBlock(text="page 5 content again")],
            score=0.7,
            metadata={"filename": "doc.pdf", "page_number": 5},
        ),
    ]
    backend = CitableRagBackend(results_a)
    tool = KnowledgeSearchTool(backend)

    first = await tool.execute(action="search", text="first query")
    assert "[1] (doc.pdf, p.5)" in first.content[0].text

    backend._results = results_b
    second = await tool.execute(action="search", text="second query")

    assert "[2] (doc.pdf, p.7)" in second.content[0].text
    assert "[1] (doc.pdf, p.5)" in second.content[0].text
    # The emitted citation list is cumulative — the frontend rebuilds the
    # whole source list from the last event alone (survives a page reload).
    assert len(second.structured_content["citations"]) == 2


def _chart_result(page: int) -> SearchResult:
    """A chart/table hit whose content IS the image — no text block, exactly
    what LocalRagBackend's image_store path returns."""
    return SearchResult(
        id=f"img-{page}",
        content=[ImageBlock(data=f"png-bytes-{page}".encode(), media_type="image/png")],
        score=0.9,
        metadata={"filename": "financials.pdf", "page_number": page},
    )


async def test_knowledge_search_tool_attaches_each_image_once_per_conversation():
    """A document holds only a handful of chart images, so every search in a
    turn retrieves the same top-k images. Re-attaching them per call re-sent
    identical pixels to the model and made the UI render the same "N charts
    generated" group once per call."""
    charts = [_chart_result(1), _chart_result(2), _chart_result(3)]
    backend = CitableRagBackend(charts)
    tool = KnowledgeSearchTool(backend)

    first = await tool.execute(action="search", text="net sales")
    assert len([b for b in first.content if isinstance(b, ImageBlock)]) == 3

    # Same images come back for a differently-worded question in the same turn.
    second = await tool.execute(action="search", text="total assets")
    assert [b for b in second.content if isinstance(b, ImageBlock)] == []
    # The model still gets the passage labelled and told why there's no image,
    # so it doesn't read the absence as "the chart is missing".
    assert "already attached earlier" in second.content[0].text
    # Citations are unaffected — all three stay in the cumulative list.
    assert len(second.structured_content["citations"]) == 3


async def test_knowledge_search_tool_deduplicates_repeated_image_within_one_batch():
    """Two hits on the same (file, page) share one citation index, so the
    image behind it must be attached once, not once per hit."""
    backend = CitableRagBackend([_chart_result(1), _chart_result(1)])
    tool = KnowledgeSearchTool(backend)

    result = await tool.execute(action="search", text="net sales")

    assert len([b for b in result.content if isinstance(b, ImageBlock)]) == 1


async def test_knowledge_search_tool_still_attaches_a_genuinely_new_image():
    """The dedupe must key on passage identity, not on "have I ever attached
    an image" — a later search reaching a new page still sends its pixels."""
    backend = CitableRagBackend([_chart_result(1)])
    tool = KnowledgeSearchTool(backend)

    await tool.execute(action="search", text="net sales")
    backend._results = [_chart_result(1), _chart_result(4)]
    second = await tool.execute(action="search", text="cash flows")

    images = [b for b in second.content if isinstance(b, ImageBlock)]
    assert len(images) == 1
    assert images[0].data == b"png-bytes-4"


async def test_knowledge_search_tool_no_structured_content_when_uncitable():
    """Results with no filename can't be linked to anything servable — no
    citation index, and no structured_content to render a fake source."""
    tool = KnowledgeSearchTool(FakeRagBackend())  # metadata={} by default

    result = await tool.execute(action="search", text="anything")

    assert result.structured_content == {}
    assert "(unlabelled)" in result.content[0].text
