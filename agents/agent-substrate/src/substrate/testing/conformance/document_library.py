"""Conformance suite for ``DocumentLibrary`` (a place documents are filed, navigated, searched and described).

Every implementation — the folder ``Library`` on SQLite or PostgreSQL, an adapter over a governed document service, any a consumer writes —
runs exactly these tests: adding the same bytes twice is one document, a listing pages by cursor, a long read continues from an offset,
words find a section and say which pages it spans, a figure can be viewed, a delete or an erasure removes exactly what it should,
one collection is invisible from another, and the enrichment primitives (``claim``, ``annotate``, ``fail``, ``set_topics``) keep the
books that ``substrate.documents.enrichment`` relies on.

Subclass it and provide the ``library`` fixture. Documents are added as ``ExtractionResult`` values, so no reader is involved. Two parts are
opt-in: ``has_embedder = True`` runs the hybrid-search test (the library was built with an embedder), and the topic tests run when the
library files topics (``library.file_topics``).
"""

from __future__ import annotations

import struct
import uuid
import zlib
from collections.abc import Sequence
from typing import ClassVar

import pytest

from substrate.documents.enrichment import (
    Described,
    DocumentBrief,
    EnrichmentUsage,
    FirstSentenceEnricher,
    Organised,
    enrich,
    reorganise,
)
from substrate.documents.protocols import DocumentLibrary
from substrate.documents.types import (
    EnrichmentState,
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
    Hits,
)

SECTION_CHARS = 1500
"""Enough prose per section that a splitter does not merge it into its neighbour."""


def prose(word: str, *, chars: int = SECTION_CHARS) -> str:
    """Sentences about ``word`` — a distinctive token the tests search for."""
    sentence = f"The {word} figures were reviewed carefully by the {word} committee this year. "
    return (sentence * (chars // len(sentence) + 1))[:chars].strip()


def png() -> bytes:
    """A 1x1 PNG, built by hand so the suite needs no imaging library."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def document(
    *pages: str, title: str | None = None, images: Sequence[ExtractedImage] = ()
) -> ExtractionResult:
    """A read result with one page per argument (a page's markdown is its text); ``images`` go on page 1."""
    return ExtractionResult(
        pages=[
            ExtractedPage(
                page_number=n,
                text=text,
                markdown=text,
                images=list(images) if n == 1 else [],
            )
            for n, text in enumerate(pages, start=1)
        ],
        markdown="\n\n".join(
            f"<!-- page {n} -->\n\n{text}" for n, text in enumerate(pages, start=1)
        ),
        engine="conformance",
        title=title,
    )


def gone_from(hits: Sequence[object]) -> set[str]:
    """The ids of the documents a search returned."""
    return {h.document.document for h in hits}  # type: ignore[attr-defined]


def unique(prefix: str = "w") -> str:
    """A word no other test used: a shared database outlives a test."""
    return f"{prefix}{uuid.uuid4().hex[:10]}"


class _Filing:
    """An enricher with a model's behaviour and none of its cost: describes every section, files the document under ``topic``."""

    name = "conformance-filing"
    version = "1"

    def __init__(self, topic: str) -> None:
        self.topic = topic

    async def enrich(self, brief: DocumentBrief, *, topics: Sequence[str]) -> Described:
        return Described(
            sections={
                s.position: f"Section {s.position} of {brief.title}."
                for s in brief.sections
            },
            card=f"{brief.title}: a conformance document.",
            topics=(self.topic,),
        )


class _Reorganiser:
    name = "conformance-organiser"
    version = "1"

    def __init__(self, topic: str) -> None:
        self.topic = topic

    async def organise(self, documents: Sequence[object]) -> Organised:
        return Organised(topics={d.document: (self.topic,) for d in documents})  # type: ignore[attr-defined]


class DocumentLibraryConformance:
    has_embedder: ClassVar[bool] = False
    """Set to ``True`` when the library under test was built with an embedder: the hybrid-search test then runs."""

    @pytest.fixture
    async def library(
        self,
    ) -> DocumentLibrary:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @pytest.fixture
    def collection(self) -> str:
        """A collection no earlier test used."""
        return f"conformance/{uuid.uuid4().hex[:12]}/documents"

    @pytest.fixture
    def other(self) -> str:
        return f"conformance/{uuid.uuid4().hex[:12]}/documents"

    # ── adding ───────────────────────────────────────────────────────────────────────────────────────────────

    async def test_adding_the_same_document_twice_is_one_document(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        source = document(prose(unique()), title="Annual report")
        first = await library.add(source, "report.md", collection=collection)
        again = await library.add(source, "report.md", collection=collection)
        assert not first.duplicate and first.title == "Annual report"
        assert again.duplicate and again.document == first.document
        assert [
            d.document for d in (await library.list(collection=collection)).documents
        ] == [first.document]
        info = await library.info(collection, first.document)
        assert info is not None and info.filename == "report.md" and info.sections >= 1

    async def test_metadata_and_resource_are_kept_with_the_document(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        added = await library.add(
            document(prose(unique())),
            "kept.md",
            collection=collection,
            resource="uploads/kept.md",
            metadata={"file_id": "f-1"},
        )
        info = await library.info(collection, added.document)
        assert info is not None
        assert info.resource == "uploads/kept.md" and info.meta.get("file_id") == "f-1"

    async def test_a_document_with_nothing_readable_is_refused_with_a_reason(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        with pytest.raises(ValueError):
            await library.add(
                ExtractionResult(pages=[], markdown="  ", engine="conformance"),
                "empty.txt",
                collection=collection,
            )
        assert (await library.list(collection=collection)).documents == ()

    # ── navigating ──────────────────────────────────────────────────────────────────────────────────────────

    async def test_a_listing_pages_by_cursor_oldest_first(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        added = [
            (
                await library.add(
                    document(prose(unique())), f"d{n}.md", collection=collection
                )
            ).document
            for n in range(3)
        ]
        first = await library.list(collection=collection, limit=2)
        assert [d.document for d in first.documents] == added[:2] and first.next
        second = await library.list(collection=collection, limit=2, after=first.next)
        assert [d.document for d in second.documents] == added[
            2:
        ] and second.next is None

    async def test_collections_counts_the_documents_under_a_prefix(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        root = collection.rsplit("/", 1)[0]
        await library.add(document(prose(unique())), "a.md", collection=f"{root}/one")
        await library.add(document(prose(unique())), "b.md", collection=f"{root}/two")
        await library.add(document(prose(unique())), "c.md", collection=f"{root}/two")
        assert await library.collections(under=root) == [
            (f"{root}/one", 1),
            (f"{root}/two", 2),
        ]

    async def test_an_outline_lists_the_sections_and_the_headings_inside_one(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        text = (
            f"## Overview\n\n{prose(unique())}\n\n"
            f"## Results\n\n### Revenue\n\n{prose(unique())}\n\n### Costs\n\n{prose(unique())}\n\n"
            f"## Outlook\n\n{prose(unique())}"
        )
        added = await library.add(
            document(text, title="Plan"), "plan.md", collection=collection
        )
        outline = await library.outline(collection=collection, document=added.document)
        assert outline is not None and outline.document.title == "Plan"
        assert len(outline.sections) == added.sections >= 2
        assert [s.position for s in outline.sections] == sorted(
            s.position for s in outline.sections
        )
        assert all(s.first_page <= s.last_page for s in outline.sections)
        inside = await library.outline(
            collection=collection, document=added.document, section=1
        )
        assert inside is not None and len(inside.sections) == 1
        assert (
            await library.outline(
                collection=collection, document=added.document, section=99
            )
            is None
        )
        assert (
            await library.outline(collection=collection, document="no-such-document")
            is None
        )

    async def test_a_long_read_continues_from_an_offset(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        pages = [
            "\n\n".join(f"page {n} paragraph {i} " + "word " * 100 for i in range(12))
            for n in range(1, 11)
        ]
        added = await library.add(document(*pages), "long.md", collection=collection)
        seen, offset, reads = "", 0, 0
        while True:
            passage = await library.read(
                collection=collection,
                document=added.document,
                pages=(1, 10),
                offset=offset,
            )
            assert passage is not None and len(passage.text) <= 24_000
            seen += passage.text
            reads += 1
            if passage.next_offset is None:
                break
            assert passage.next_offset > offset
            offset = passage.next_offset
        assert reads >= 2
        assert "page 1 paragraph 0 " in seen and "page 10 paragraph 11 " in seen

    async def test_reading_a_section_or_a_page_range_and_what_is_not_there(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        added = await library.add(
            document(prose(unique())), "short.md", collection=collection
        )
        section = await library.read(
            collection=collection, document=added.document, section=1
        )
        assert section is not None and section.text and section.next_offset is None
        assert (
            await library.read(
                collection=collection, document=added.document, section=99
            )
            is None
        )
        assert (
            await library.read(
                collection=collection, document="no-such-document", section=1
            )
            is None
        )

    # ── searching ───────────────────────────────────────────────────────────────────────────────────────────

    async def test_find_matches_words_and_relaxes_to_any_word(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        word = unique("rotterdam")
        added = await library.add(
            document(prose(word), title="Ports"), "ports.md", collection=collection
        )
        await library.add(
            document(prose(unique("other"))), "other.md", collection=collection
        )
        hits = await library.find(collection=collection, query=word)
        assert isinstance(hits, Hits) and hits
        assert hits[0].document.document == added.document and word in hits[0].snippet
        relaxed = await library.find(
            collection=collection, query=f"{word} zebra{unique()}"
        )
        assert relaxed and relaxed[0].document.document == added.document
        scoped = await library.find(
            collection=collection, query=word, document="another-document"
        )
        assert scoped == []
        if (
            not self.has_embedder
        ):  # similarity always has a nearest neighbour; words can find nothing
            assert (
                await library.find(collection=collection, query=unique("nothing")) == []
            )

    async def test_a_hit_names_the_pages_of_its_section(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        word = unique("citation")
        added = await library.add(
            document(
                "First page, nothing special.",
                f"Second page, about {word} only.",
                "Third page.",
            ),
            "cited.md",
            collection=collection,
        )
        hits = await library.find(collection=collection, query=word)
        assert hits and hits[0].document.document == added.document
        section = hits[0].section
        assert section.first_page <= 2 <= section.last_page
        page = await library.read(
            collection=collection, document=added.document, pages=(2, 2)
        )
        assert (
            page is not None
            and word in page.text
            and "<!-- page 1 -->" not in page.text
        )

    async def test_find_by_meaning_and_words_when_the_library_has_an_embedder(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        if not self.has_embedder:
            pytest.skip("this library has no embedder")
        word = unique("hybrid")
        added = await library.add(
            document(prose(word)), "hybrid.md", collection=collection
        )
        assert await library.embed_missing(collection=collection) == 0
        hits = await library.find(collection=collection, query=word)
        assert hits and hits[0].document.document == added.document

    # ── figures ─────────────────────────────────────────────────────────────────────────────────────────────

    async def test_a_figure_can_be_viewed_by_id_or_by_page(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        figure = ExtractedImage(
            data=png(),
            id="fig-1",
            label=ExtractedImageLabel.CHART,
            caption="Revenue by region",
            page_number=1,
        )
        added = await library.add(
            document(prose(unique()), images=[figure]),
            "chart.md",
            collection=collection,
        )
        assert added.images == 1
        by_page = await library.view(
            collection=collection, document=added.document, page=1
        )
        assert (
            by_page is not None
            and by_page.data[:4] == b"\x89PNG"
            and by_page.image == "fig-1"
        )
        by_id = await library.view(collection=collection, image="fig-1")
        assert by_id is not None and by_id.data == by_page.data
        assert (
            await library.view(collection=collection, document=added.document, page=9)
            is None
        )
        assert await library.view(collection=collection, image="no-such-figure") is None

    # ── removing ────────────────────────────────────────────────────────────────────────────────────────────

    async def test_delete_removes_one_document_and_only_that_one(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        gone_word, kept_word = unique("gone"), unique("kept")
        gone = await library.add(
            document(prose(gone_word)), "gone.md", collection=collection
        )
        kept = await library.add(
            document(prose(kept_word)), "kept.md", collection=collection
        )
        assert (
            await library.delete(collection=collection, document=gone.document) is True
        )
        assert await library.info(collection, gone.document) is None
        found = await library.find(collection=collection, query=gone_word)
        assert gone.document not in {h.document.document for h in found}
        assert (
            await library.read(collection=collection, document=gone.document, section=1)
            is None
        )
        assert [
            d.document for d in (await library.list(collection=collection)).documents
        ] == [kept.document]
        assert await library.find(collection=collection, query=kept_word)
        assert (
            await library.delete(collection=collection, document=gone.document) is False
        )

    async def test_erase_under_removes_a_prefix_and_leaves_the_others(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        root = collection.rsplit("/", 1)[0]
        sibling = f"{root}-sibling/documents"
        a = await library.add(document(prose(unique())), "a.md", collection=f"{root}/a")
        await library.add(document(prose(unique())), "b.md", collection=f"{root}/b")
        keep = await library.add(document(prose(unique())), "c.md", collection=sibling)
        assert await library.erase_under(root) == 2
        assert await library.info(f"{root}/a", a.document) is None
        assert (await library.list(collection=f"{root}/b")).documents == ()
        assert [
            d.document for d in (await library.list(collection=sibling)).documents
        ] == [keep.document]
        assert await library.erase_under(root) == 0

    async def test_one_collection_cannot_see_another(
        self, library: DocumentLibrary, collection: str, other: str
    ) -> None:
        word = unique("wall")
        figure = ExtractedImage(data=png(), id="fig-wall", page_number=1)
        added = await library.add(
            document(prose(word), images=[figure]), "wall.md", collection=collection
        )
        assert (await library.list(collection=other)).documents == ()
        assert await library.info(other, added.document) is None
        assert gone_from(await library.find(collection=other, query=word)) == set()
        assert await library.outline(collection=other, document=added.document) is None
        assert (
            await library.read(collection=other, document=added.document, section=1)
            is None
        )
        assert await library.view(collection=other, image="fig-wall") is None
        assert await library.delete(collection=other, document=added.document) is False
        assert await library.info(collection, added.document) is not None

    # ── enrichment ──────────────────────────────────────────────────────────────────────────────────────────

    async def test_a_document_is_pending_until_it_is_described_by_the_enricher(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        enricher = FirstSentenceEnricher()
        added = await library.add(
            document(prose(unique())), "p.md", collection=collection
        )
        assert await library.pending(collection=collection, enricher=enricher) == [
            added.document
        ]
        assert (collection, added.document) in await library.pending_all(
            enricher=enricher, matching=f"{collection}%"
        )
        done = await enrich(
            library, collection=collection, document=added.document, enricher=enricher
        )
        assert done.state == "done"
        assert await library.pending(collection=collection, enricher=enricher) == []
        newer = FirstSentenceEnricher()
        newer.version = "2"
        assert await library.pending(collection=collection, enricher=newer) == [
            added.document
        ]

    async def test_enriching_twice_with_the_same_enricher_writes_once(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        enricher = FirstSentenceEnricher()
        added = await library.add(
            document(prose(unique())), "once.md", collection=collection
        )
        first = await enrich(
            library, collection=collection, document=added.document, enricher=enricher
        )
        state = await library.enrichment_state(
            collection=collection, document=added.document
        )
        again = await enrich(
            library, collection=collection, document=added.document, enricher=enricher
        )
        assert first.state == again.state == "done" and again.card == first.card
        after = await library.enrichment_state(
            collection=collection, document=added.document
        )
        assert (
            state is not None
            and after is not None
            and after.updated_at == state.updated_at
        )
        assert (
            await enrich(
                library,
                collection=collection,
                document="no-such-document",
                enricher=enricher,
            )
        ).state == "missing"
        assert (
            await enrich(
                library, collection=collection, document=added.document, enricher=None
            )
        ).state == "skipped"

    async def test_a_claim_hides_a_document_and_a_failure_brings_it_back_until_three_attempts(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        enricher = FirstSentenceEnricher()
        added = await library.add(
            document(prose(unique())), "claim.md", collection=collection
        )
        ids = {"collection": collection, "document": added.document}
        assert await library.enrichment_state(**ids) is None

        await library.claim(**ids, enricher=enricher, attempts=1)
        state = await library.enrichment_state(**ids)
        assert (
            isinstance(state, EnrichmentState)
            and state.state == "running"
            and state.attempts == 1
        )
        assert state.enricher == enricher.name and state.version == enricher.version
        assert await library.pending(collection=collection, enricher=enricher) == []
        info = await library.info(collection, added.document)
        assert info is not None and info.enrichment == "running"

        await library.fail(
            **ids, enricher=enricher, attempts=2, error="the model was down"
        )
        failed = await library.enrichment_state(**ids)
        assert failed is not None and failed.state == "failed" and failed.attempts == 2
        assert await library.pending(collection=collection, enricher=enricher) == [
            added.document
        ]
        await library.fail(
            **ids, enricher=enricher, attempts=3, error="the model was down"
        )
        assert await library.pending(collection=collection, enricher=enricher) == []

    async def test_annotating_stores_the_descriptions_where_navigation_and_search_find_them(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        enricher = FirstSentenceEnricher()
        added = await library.add(
            document(prose(unique())), "annotated.md", collection=collection
        )
        brief = await library.brief(collection=collection, document=added.document)
        assert (
            brief is not None
            and brief.title
            and brief.sections
            and brief.sections[0].text
        )
        assert (
            await library.brief(collection=collection, document="no-such-document")
            is None
        )

        word = unique("zeppelin")
        sections = {
            s.position: f"About the {word} number {s.position}." for s in brief.sections
        }
        for _ in range(2):  # writing the same thing twice leaves one copy
            await library.annotate(
                collection=collection,
                document=added.document,
                card=f"A document about {word}.",
                sections=sections,
                topics=[],
                enricher=enricher,
                attempts=1,
                usage=_usage(),
            )
        info = await library.info(collection, added.document)
        assert info is not None and info.description == f"A document about {word}."
        assert info.enrichment == "done"
        state = await library.enrichment_state(
            collection=collection, document=added.document
        )
        assert state is not None and state.state == "done"
        outline = await library.outline(collection=collection, document=added.document)
        assert outline is not None
        assert [s.description for s in outline.sections] == [
            sections[s.position] for s in outline.sections
        ]
        hits = await library.find(collection=collection, query=word)
        assert hits and hits[0].document.document == added.document
        listing = await library.list(collection=collection)
        assert listing.documents[0].description == f"A document about {word}."
        # the section's own text is untouched
        passage = await library.read(
            collection=collection, document=added.document, section=1
        )
        assert passage is not None and word not in passage.text

    # ── topics ──────────────────────────────────────────────────────────────────────────────────────────────

    async def test_documents_are_filed_in_a_topic_tree_that_browse_walks(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        if not library.file_topics:
            pytest.skip("this library does not file topics")
        enricher = _Filing("Finance/Earnings")
        added = await library.add(
            document(prose(unique(), chars=4000)), "q3.md", collection=collection
        )
        done = await enrich(
            library, collection=collection, document=added.document, enricher=enricher
        )
        assert done.state == "done" and done.topics == ("Finance/Earnings",)
        top = await library.browse(collection=collection)
        assert (
            top is not None
            and top.children == (("Finance", 1),)
            and top.documents == ()
        )
        leaf = await library.browse(collection=collection, topic="Finance/Earnings")
        assert leaf is not None and [d.document for d in leaf.documents] == [
            added.document
        ]
        assert await library.browse(collection=collection, topic="Nowhere") is None
        info = await library.info(collection, added.document)
        assert info is not None and info.topics == ("Finance/Earnings",)
        assert await library.needs_reorganising(collection) is False

    async def test_set_topics_refiles_documents_without_moving_them(
        self, library: DocumentLibrary, collection: str
    ) -> None:
        if not library.file_topics:
            pytest.skip("this library does not file topics")
        first = await library.add(
            document(prose(unique(), chars=4000)), "one.md", collection=collection
        )
        second = await library.add(
            document(prose(unique(), chars=4000)), "two.md", collection=collection
        )
        for added in (first, second):
            await enrich(
                library,
                collection=collection,
                document=added.document,
                enricher=_Filing(f"Old/{added.document[:3]}"),
            )
        done = await reorganise(
            library, collection=collection, organiser=_Reorganiser("Merged/Everything")
        )
        assert done.state == "done" and done.moved == 2
        leaf = await library.browse(collection=collection, topic="Merged/Everything")
        assert leaf is not None and {d.document for d in leaf.documents} == {
            first.document,
            second.document,
        }
        assert await library.browse(collection=collection, topic="Old") is None
        # the documents themselves did not move
        assert (
            await library.read(
                collection=collection, document=first.document, section=1
            )
            is not None
        )
        again = await reorganise(
            library, collection=collection, organiser=_Reorganiser("Merged/Everything")
        )
        assert again.state == "done" and again.moved == 0


def _usage() -> EnrichmentUsage:
    return EnrichmentUsage()


__all__ = ["DocumentLibraryConformance"]
