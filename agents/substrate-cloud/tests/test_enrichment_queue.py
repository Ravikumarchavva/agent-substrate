"""``EnrichmentQueue``: describes documents in the background, once each, within a tenant's daily budget, and never raises into the caller."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from substrate.documents import Described, EnrichmentUsage, Library, Reader
from substrate.documents.types import ExtractedPage, ExtractionResult
from substrate.stores import Store

from substrate_cloud.documents_library import EnrichmentQueue, build_enricher, tenant_of

C = "tenants/acme/knowledge/kb"


class Writer:
    name, version = "w", "1"

    def __init__(self, tokens: int = 1000, fail: bool = False) -> None:
        self.tokens, self.fail, self.calls = tokens, fail, 0

    async def enrich(self, brief, *, topics):
        self.calls += 1
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("boom")
        return Described(
            sections={s.position: f"About {s.title}." for s in brief.sections},
            card="A document.",
            usage=EnrichmentUsage(input_tokens=self.tokens, output_tokens=0, calls=1),
        )


def _doc(extra: str = "") -> ExtractionResult:
    text = "## Part\n\n" + ("Net sales were $119,575 million. " * 200) + extra
    return ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x")], markdown=text, engine="t"
    )


async def _library(tmp_path, writer):
    store = Store.at(tmp_path / "s")
    await store.start()
    return store, Library(store, reader=Reader(isolate=False), enricher=writer)


async def test_submit_describes_in_the_background_and_only_once(tmp_path):
    writer = Writer()
    store, library = await _library(tmp_path, writer)
    try:
        a = await library.add(_doc(), "a.md", collection=C)
        queue = EnrichmentQueue(concurrency=2, daily_tokens=0)
        queue.submit(library, C, a.document)
        queue.submit(library, C, a.document)  # already queued: ignored
        await queue.drain()
        assert writer.calls == 1
        assert (await library.info(C, a.document)).description == "A document."
    finally:
        await store.aclose()


async def test_a_failing_model_never_raises_into_the_caller(tmp_path):
    store, library = await _library(tmp_path, Writer(fail=True))
    try:
        a = await library.add(_doc(), "a.md", collection=C)
        queue = EnrichmentQueue(concurrency=1, daily_tokens=0)
        queue.submit(library, C, a.document)
        await queue.drain()
        assert (await library.info(C, a.document)).enrichment == "failed"
    finally:
        await store.aclose()


async def test_a_tenant_over_its_daily_budget_is_paused_and_others_are_not(tmp_path):
    writer = Writer(tokens=600)
    store, library = await _library(tmp_path, writer)
    try:
        docs = [
            await library.add(_doc(f" {n}"), f"d{n}.md", collection=C) for n in range(3)
        ]
        other = await library.add(
            _doc(" other"), "o.md", collection="tenants/zed/knowledge/kb"
        )
        queue = EnrichmentQueue(concurrency=1, daily_tokens=1000)
        for d in docs:
            queue.submit(library, C, d.document)
        queue.submit(library, "tenants/zed/knowledge/kb", other.document)
        await queue.drain()
        # 600 + 600 >= 1000: the third document of acme waits; zed has its own budget
        described = [(await library.info(C, d.document)).description for d in docs]
        assert described.count("A document.") == 2 and described.count(None) == 1
        assert (
            await library.info("tenants/zed/knowledge/kb", other.document)
        ).description == "A document."
    finally:
        await store.aclose()


async def test_sweep_queues_what_still_needs_describing(tmp_path):
    writer = Writer()
    store, library = await _library(tmp_path, writer)
    try:
        for n in range(3):
            await library.add(_doc(f" {n}"), f"d{n}.md", collection=C)
        queue = EnrichmentQueue(concurrency=2, daily_tokens=0)
        assert await queue.sweep(library) == 3
        await queue.drain()
        assert writer.calls == 3 and await queue.sweep(library) == 0
    finally:
        await store.aclose()


def test_the_tenant_comes_from_the_collection_path():
    assert tenant_of("tenants/acme/users/u/conversations/c/documents") == "acme"
    assert tenant_of("elsewhere/x") == ""


def test_no_key_for_the_model_means_no_descriptions_not_a_failed_start():
    cfg = SimpleNamespace(
        DOCUMENT_SUMMARY_ENABLED=True,
        DOCUMENT_SUMMARY_MODEL="openai/gpt-5.4-mini",
        CHAT_MODEL="google/gemini-3.1-flash-lite",
        DOCUMENT_SUMMARY_CONCURRENCY=4,
    )
    assert build_enricher(cfg, {"openai": ""}) is None
    cfg.DOCUMENT_SUMMARY_ENABLED = False
    assert build_enricher(cfg, {"openai": "sk-test"}) is None
    cfg.DOCUMENT_SUMMARY_ENABLED = True
    enricher = build_enricher(cfg, {"openai": "sk-test"})
    assert enricher is not None and enricher.name.startswith("summariser-")


async def test_a_topic_tree_that_grew_too_wide_is_redrawn_after_describing(tmp_path):
    """After a knowledge-base document is described, the queue checks the tree and has the whole of it redrawn if one topic outgrew browsing."""
    from unittest.mock import AsyncMock, MagicMock

    library = MagicMock()
    library.enrich = AsyncMock(
        return_value=MagicMock(
            state="done", usage=MagicMock(input_tokens=0, output_tokens=0)
        )
    )
    library.needs_reorganising = AsyncMock(return_value=True)
    library.reorganise = AsyncMock(
        return_value=MagicMock(
            state="done",
            moved=5,
            usage=MagicMock(input_tokens=900, output_tokens=100, cost_usd=0.001),
        )
    )
    queue = EnrichmentQueue(concurrency=1, daily_tokens=0, redis=None)
    queue.submit(library, "tenants/acme/knowledge/hr/library", "doc1")
    await queue.drain()
    library.reorganise.assert_awaited_once_with(
        collection="tenants/acme/knowledge/hr/library"
    )

    library.needs_reorganising.return_value = False
    library.reorganise.reset_mock()
    queue.submit(library, "tenants/acme/knowledge/hr/library", "doc2")
    await queue.drain()
    library.reorganise.assert_not_awaited()
