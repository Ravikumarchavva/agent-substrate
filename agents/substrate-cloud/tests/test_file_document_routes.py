"""``GET /files/{id}/document`` and ``POST /files/{id}/document/describe``: what the library knows about an upload, and asking for it again.
Route functions are called directly with stubs, as in ``test_files_upload_caps.py``."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from substrate.documents import DocumentInfo, Outline, SectionInfo
from substrate_cloud.monolith.routes import files as files_module
from substrate_cloud.monolith.routes.files import (
    describe_file_document,
    get_file_document,
    list_thread_documents,
)

FILE_ID = uuid.uuid4()
THREAD = uuid.uuid4()
CLAIMS = SimpleNamespace(tenant_id="acme", sub="u1")


def _meta(**over):
    base = dict(
        thread_id=THREAD,
        original_name="q1.pdf",
        checksum_sha256="ab" * 32,
        user_id=None,
    )
    return SimpleNamespace(**{**base, **over})


def _outline(description: str | None = "A quarterly report.") -> Outline:
    info = DocumentInfo(
        document="q1-abc",
        title="Q1",
        filename="q1.pdf",
        pages=3,
        sections=1,
        images=0,
        needs_ocr=(),
        engine="t",
        resource=None,
        description=description,
        topics=("Finance/Earnings",),
        enrichment="done" if description else "none",
    )
    section = SectionInfo(
        1, "Results", ("Q1",), 1, 3, 400, description="Net sales rose."
    )
    return Outline(document=info, sections=(section,))


def _ctx(outline: Outline | None = None):
    library = MagicMock()
    library.document_id = MagicMock(return_value="q1-abc")
    library.outline = AsyncMock(return_value=outline)
    return SimpleNamespace(library=library, enrichment=MagicMock())


@pytest.fixture(autouse=True)
def _file(monkeypatch):
    async def get_meta(file_id, db, claims):
        return _meta()

    monkeypatch.setattr(files_module, "_get_meta", get_meta)


async def test_it_returns_the_card_topics_and_section_descriptions():
    out = await get_file_document(FILE_ID, _ctx(_outline()), CLAIMS, MagicMock())
    assert out["state"] == "done" and out["generated"] is True
    assert out["description"] == "A quarterly report."
    assert out["topics"] == ["Finance/Earnings"]
    assert out["sections"][0]["description"] == "Net sales rose."


async def test_an_undescribed_document_says_so_instead_of_inventing_a_card():
    out = await get_file_document(FILE_ID, _ctx(_outline(None)), CLAIMS, MagicMock())
    assert out["state"] == "none" and out["generated"] is False
    assert out["description"] is None


async def test_a_file_that_was_never_filed_is_404():
    with pytest.raises(HTTPException) as err:
        await get_file_document(FILE_ID, _ctx(None), CLAIMS, MagicMock())
    assert err.value.status_code == 404


async def test_describe_queues_a_forced_run_in_this_conversations_collection():
    ctx = _ctx(_outline())
    out = await describe_file_document(FILE_ID, ctx, CLAIMS, MagicMock())
    assert out == {"state": "running"}
    (library, collection, document), kwargs = ctx.enrichment.submit.call_args
    assert library is ctx.library and document == "q1-abc" and kwargs == {"force": True}
    assert collection.startswith("tenants/acme/") and str(THREAD) in collection


async def test_describe_is_503_when_summaries_are_off():
    ctx = _ctx(_outline())
    ctx.enrichment = None
    with pytest.raises(HTTPException) as err:
        await describe_file_document(FILE_ID, ctx, CLAIMS, MagicMock())
    assert err.value.status_code == 503


async def test_the_conversations_documents_are_listed_with_their_cards(monkeypatch):
    async def owned(db, thread_id, claims):
        return object()

    monkeypatch.setattr(files_module, "get_owned_thread", owned)
    info = _outline().document
    info = DocumentInfo(**{**info.__dict__, "meta": {"file_id": str(FILE_ID)}})
    ctx = _ctx(_outline())
    ctx.library.list = AsyncMock(return_value=SimpleNamespace(documents=(info,)))
    out = await list_thread_documents(THREAD, ctx, CLAIMS, MagicMock())
    assert out == [
        {
            "file_id": str(FILE_ID),
            "name": "q1.pdf",
            "state": "done",
            "description": "A quarterly report.",
            "topics": ["Finance/Earnings"],
            "pages": 3,
        }
    ]
