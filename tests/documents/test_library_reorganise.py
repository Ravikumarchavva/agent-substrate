"""``Library.reorganise``: redrawing the whole topic tree changes only where documents are filed, never the documents, and a model that
fails or invents things leaves the tree as it was. Runs on both databases like the other ``Library`` tests."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from substrate.documents import (
    Described,
    DocumentBrief,
    EnrichmentUsage,
    FiledDocument,
    Library,
    Organised,
    Reader,
    okf,
)
from substrate.documents.enrichment import MAX_TOPIC_CHILDREN
from substrate.documents.types import ExtractedPage, ExtractionResult

C = "tenants/acme/knowledge/handbook"


def _document(n: int) -> ExtractionResult:
    return ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x")],
        markdown=f"## Part {n}\n\n<!-- page 1 -->\n\n" + f"Fact number {n}. " * 300,
        engine="test",
    )


class Filer:
    """Enriches each document and files it under a topic of its own, so the tree grows one branch per document (the drift
    reorganising is for)."""

    name = "filer"
    version = "1"

    async def enrich(self, brief: DocumentBrief, *, topics: Sequence[str]) -> Described:
        n = brief.filename.split(".")[0]
        return Described(
            sections={s.position: f"About {n}." for s in brief.sections},
            card=f"Card of {n}.",
            topics=(f"Misc/Topic {n}",),
            usage=EnrichmentUsage(),
        )


class Organiser:
    name = "organiser"
    version = "1"

    def __init__(self, answer=None, fail: Exception | None = None):
        self.answer, self.fail = answer, fail
        self.seen: list[FiledDocument] = []

    async def organise(self, documents: Sequence[FiledDocument]) -> Organised:
        self.seen = list(documents)
        if self.fail:
            raise self.fail
        return Organised(self.answer(documents))


@pytest.fixture
def library(store):
    return Library(
        store, reader=Reader(isolate=False), enricher=Filer(), file_topics=True
    )


async def filed(library: Library, count: int) -> list[str]:
    ids = []
    for n in range(count):
        a = await library.add(_document(n), f"{n}.md", collection=C)
        await library.enrich(collection=C, document=a.document)
        ids.append(a.document)
    return ids


async def topics_of(library: Library, document: str) -> tuple[str, ...]:
    return (await library.info(C, document)).topics


async def test_the_organiser_sees_every_card_and_where_it_is_filed_now(library):
    ids = await filed(library, 3)
    organiser = Organiser(lambda docs: {})
    await library.reorganise(collection=C, organiser=organiser)
    assert [d.document for d in organiser.seen] == ids
    assert organiser.seen[0].card == "Card of 0." and organiser.seen[0].topics == (
        "Misc/Topic 0",
    )


async def test_it_moves_documents_between_topics_without_touching_them(library):
    ids = await filed(library, 4)
    text_before = (await library.read(collection=C, document=ids[0], section=1)).text
    organiser = Organiser(
        lambda docs: {d.document: ("Handbook/Policies",) for d in docs}
    )
    done = await library.reorganise(collection=C, organiser=organiser)
    assert done.state == "done" and done.moved == 4 and done.topics == 1

    assert [await topics_of(library, d) for d in ids] == [("Handbook/Policies",)] * 4
    tree = await library.browse(collection=C)
    assert [name for name, _n in tree.children] == ["Handbook"]
    inner = await library.browse(collection=C, topic="Handbook/Policies")
    assert {d.document for d in inner.documents} == set(ids)
    assert (
        await library.browse(collection=C, topic="Misc") is None
    )  # the old branch is gone
    # the document is where it was, its text is what it was, and its own index records the new topic
    assert (
        await library.read(collection=C, document=ids[0], section=1)
    ).text == text_before
    index = okf.parse(
        (await library._files.download(f"{C}/{ids[0]}/index.md")).decode()
    )
    assert index.extra["topics"] == ["Handbook/Policies"]


async def test_what_changed_is_written_to_the_topic_log(library):
    ids = await filed(library, 2)
    await library.reorganise(
        collection=C, organiser=Organiser(lambda docs: {ids[0]: ("Handbook",)})
    )
    log = (await library._files.download(f"{C}/_topics/log.md")).decode()
    assert f"{ids[0]}: Misc/Topic 0 → Handbook" in log and "organiser 1" in log


async def test_a_model_that_fails_leaves_the_tree_as_it_was(library):
    ids = await filed(library, 3)
    done = await library.reorganise(
        collection=C, organiser=Organiser(fail=RuntimeError("503 from the provider"))
    )
    assert done.state == "failed" and "503" not in (done.error or "")
    assert [await topics_of(library, d) for d in ids] == [
        (f"Misc/Topic {n}",) for n in range(3)
    ]


async def test_documents_the_model_made_up_are_ignored_and_paths_are_cleaned(library):
    ids = await filed(library, 2)
    organiser = Organiser(
        lambda docs: {
            ids[0]: ("[evil](http://x.test)/<b>Safe</b>/A/B/C/D",),
            "not-a-document": ("Elsewhere",),
            ids[1]: ("",),  # nothing usable: keeps what it has
        }
    )
    done = await library.reorganise(collection=C, organiser=organiser)
    assert done.state == "done" and done.moved == 1
    (topic,) = await topics_of(library, ids[0])
    assert "http" not in topic and "<" not in topic and topic.count("/") <= 2
    assert await topics_of(library, ids[1]) == ("Misc/Topic 1",)
    assert await library.browse(collection=C, topic="Elsewhere") is None


async def test_a_tree_that_grew_too_wide_asks_to_be_redrawn(library):
    await filed(library, MAX_TOPIC_CHILDREN)
    assert not await library.needs_reorganising(C)  # exactly at the limit
    await filed_more(library)
    assert await library.needs_reorganising(C)
    await library.reorganise(
        collection=C,
        organiser=Organiser(
            lambda docs: {d.document: ("Handbook/Policies",) for d in docs}
        ),
    )
    assert not await library.needs_reorganising(C)


async def filed_more(library: Library) -> None:
    a = await library.add(_document(99), "99.md", collection=C)
    await library.enrich(collection=C, document=a.document)


async def test_without_an_organiser_or_with_too_few_documents_nothing_happens(store):
    plain = Library(store, reader=Reader(isolate=False), file_topics=True)
    assert (await plain.reorganise(collection=C)).state == "skipped"
    library = Library(
        store, reader=Reader(isolate=False), enricher=Filer(), file_topics=True
    )
    await filed(library, 1)
    organiser = Organiser(lambda docs: {})
    assert (
        await library.reorganise(collection=C, organiser=organiser)
    ).state == "skipped"
    assert organiser.seen == []


async def test_a_library_that_does_not_file_topics_never_reorganises(store):
    library = Library(store, reader=Reader(isolate=False), enricher=Filer())
    await filed(library, 3)
    assert not await library.needs_reorganising(C)
    done = await library.reorganise(collection=C, organiser=Organiser(lambda d: {}))
    assert done.state == "skipped"
