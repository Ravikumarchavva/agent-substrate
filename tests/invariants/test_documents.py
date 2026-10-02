"""Invariant register — reading documents (rows I32–I34).

What a user hands an agent is untrusted bytes. Three promises hold however the document is built: a page that is only a picture is never
returned silently empty, a parser crash or a memory bomb cannot reach the host, and no hostile file makes ``Reader`` raise.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from substrate.documents import Reader
from substrate.documents.types import ReadLimits
from tests.documents._files import (
    deeply_nested_docx,
    entity_bomb_docx,
    external_entity_docx,
    fixture,
    many_members_docx,
    zip_bomb_docx,
)


async def test_a_page_that_is_only_a_picture_is_recognised_or_reported_never_silently_empty() -> (
    None
):
    """A scanned page comes back with its text (OCR available) or listed in ``needs_ocr`` (it is not) — never as an empty page with no note."""
    for ocr in ("auto", None):
        result = await Reader(ocr=ocr, isolate=False).read(
            fixture("scanned_page.pdf"), "scan.pdf"
        )
        page = result.pages[0]
        assert result.success and (
            "4417" in page.text or (page.needs_ocr and 1 in result.needs_ocr)
        )


def test_an_isolated_read_never_loads_the_parser_or_the_ocr_runtime_into_the_host() -> (
    None
):
    """With isolation on (the default) the PDF parser and the OCR runtime run in a worker process: the host's modules never include them."""
    program = textwrap.dedent(
        """
        import asyncio, sys
        from substrate.documents import Reader
        data = open("tests/fixtures/documents/scanned_page.pdf", "rb").read()
        assert asyncio.run(Reader().read(data, "s.pdf")).success
        loaded = {m.split(".")[0] for m in sys.modules}
        assert not loaded & {"pypdfium2", "rapidocr", "onnxruntime", "cv2"}, loaded & {"pypdfium2", "rapidocr", "onnxruntime", "cv2"}
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=180
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    "make",
    [
        zip_bomb_docx,
        entity_bomb_docx,
        external_entity_docx,
        deeply_nested_docx,
        many_members_docx,
    ],
)
async def test_a_hostile_document_is_a_failed_result_never_an_exception(make) -> None:
    """A zip bomb, an entity bomb, an external entity, absurd nesting and a member flood each come back as ``success=False`` with a reason."""
    result = await Reader(limits=ReadLimits(timeout_s=20)).read(make(), "evil.docx")
    assert not result.success and result.error


async def test_the_documents_tool_takes_its_collection_from_the_run_scope_and_has_no_way_to_open_a_path(
    tmp_path,
) -> None:
    """A model cannot name a collection, a path or an ingest: the tool's schema has none, extra arguments are ignored, and what it can reach is
    decided by the authenticated scope alone — so one conversation's documents are invisible to another's, whatever a document tells the model."""
    from types import SimpleNamespace

    from substrate.documents import (
        DocumentsTool,
        ExtractedPage,
        ExtractionResult,
        Library,
    )
    from substrate.stores import Store
    from substrate.types.run import RunScope

    store = Store.at(tmp_path / "store")
    await store.start()
    try:
        library = Library(store)
        doc = ExtractionResult(
            pages=[ExtractedPage(page_number=1, text="t")],
            markdown="<!-- page 1 -->\n\n" + "secret " * 300,
            engine="t",
        )
        await library.add(
            doc, "mine.md", collection="tenants/a/conversations/1/documents"
        )
        await library.add(
            doc, "theirs.md", collection="tenants/b/conversations/2/documents"
        )
        tool = DocumentsTool(
            library,
            collection=lambda s: (
                f"tenants/{s.tenant_id}/conversations/{s.thread_id}/documents"
            ),
        )
        properties = tool.input_schema["properties"]
        assert (
            not {"collection", "path", "file", "url", "ingest"} & set(properties)
            and "ingest" not in properties["action"]["enum"]
        )
        ctx = SimpleNamespace(scope=RunScope(tenant_id="a", thread_id="1"))
        listed = await tool.execute(
            ctx=ctx,
            action="list",
            collection="tenants/b/conversations/2/documents",
            path="/etc/passwd",
        )
        assert "mine" in listed.text and "theirs" not in listed.text
        assert (
            await tool.execute(
                ctx=ctx, action="read", document="/etc/passwd", section=1
            )
        ).is_error
    finally:
        await store.aclose()


async def test_the_catalog_is_derived_from_the_bundle_and_can_be_rebuilt(
    tmp_path,
) -> None:
    """Delete every catalog row and ``reindex`` brings back the same outline and the same search hits from the markdown files alone — the bundle
    is the source of truth, so a lost or corrupted catalog is a rebuild, not a data loss."""
    from substrate.documents import Library, Reader
    from substrate.stores import Store

    store = Store.at(tmp_path / "store")
    await store.start()
    try:
        library = Library(store, reader=Reader(isolate=False))
        collection = "tenants/a/conversations/1/documents"
        added = await library.add(
            fixture("sample.docx"), "q3.docx", collection=collection
        )
        before = (
            await library.outline(collection=collection, document=added.document),
            await library.find(collection=collection, query="Rotterdam"),
        )

        async def wipe(tx) -> None:
            for table in ("library_sections", "library_images", "library_documents"):
                await tx.execute(f"DELETE FROM {table}")

        await library._run(wipe)
        assert await library.reindex(collection=collection) == 1
        after = (
            await library.outline(collection=collection, document=added.document),
            await library.find(collection=collection, query="Rotterdam"),
        )
        assert [(s.position, s.title) for s in after[0].sections] == [
            (s.position, s.title) for s in before[0].sections
        ]
        assert [h.snippet for h in after[1]] == [h.snippet for h in before[1]]
    finally:
        await store.aclose()


def test_a_knowledge_base_collection_is_always_under_its_tenants_prefix() -> None:
    """The collection of a knowledge base is derived from the authenticated tenant and a validated name, so it is inside that tenant's prefix
    (where its erasure reaches it) and no name — a path, another tenant's collection — can make it point somewhere else."""
    from substrate.workspace.layout import knowledge_collection, tenant_prefix

    assert knowledge_collection("acme", "hr") == "tenants/acme/knowledge/hr/library"
    assert knowledge_collection("acme", "hr").startswith(tenant_prefix("acme") + "/")
    for hostile in [
        "../evil/hr",
        "evil/knowledge/hr",
        "tenants/evil/knowledge/hr/library",
        "a/b",
        "",
        "..",
    ]:
        with pytest.raises(ValueError):
            knowledge_collection("acme", hostile)
