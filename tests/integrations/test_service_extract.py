"""``runtimes/document_intelligence/extract.py::extract_document`` — the
single entry point every Phase 2 call site now shares. Mocks
``ExtractionClient`` so this never needs a real service; local fallback is
exercised against the real ``RawTextEngine`` (pypdfium2), same as
``test_raw_text_engine.py``."""

from __future__ import annotations

import pypdfium2 as pdfium
import pytest

from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.runtimes.document_intelligence import extract as extract_mod
from substrate.runtimes.document_intelligence.client import (
    ExtractedImage,
    ExtractedPageText,
    ExtractResponse,
)


def _real_pdf_bytes(text: str = "hello world") -> bytes:
    import io

    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


async def test_no_endpoint_runs_local_extraction_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_if_called(*a, **kw):
        raise AssertionError("ExtractionClient must not be constructed with no endpoint")

    monkeypatch.setattr(extract_mod, "ExtractionClient", _fail_if_called)

    result = await extract_mod.extract_document(
        _real_pdf_bytes(), "doc.pdf", "application/pdf", endpoint=None
    )
    assert result.engine == "raw_text"


async def test_endpoint_success_returns_service_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def extract(self, data, filename, content_type, *, timeout_s=None):
            return ExtractResponse(
                success=True,
                text="from service",
                pages=[ExtractedPageText(page_number=1, text="from service", markdown="from service")],
                images=[],
                engine="paddleocr-vl",
                page_count=1,
                markdown="from service",
            )

    monkeypatch.setattr(extract_mod, "ExtractionClient", _FakeClient)

    result = await extract_mod.extract_document(
        b"data",
        "doc.pdf",
        "application/pdf",
        endpoint=InferenceEndpoint(model="compatible/whatever", base_url="http://svc:8080"),
    )
    assert result.engine == "paddleocr-vl"
    assert result.pages[0].text == "from service"


async def test_endpoint_failure_falls_back_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def extract(self, data, filename, content_type, *, timeout_s=None):
            return ExtractResponse(success=False, error="service down")

    monkeypatch.setattr(extract_mod, "ExtractionClient", _FakeClient)

    result = await extract_mod.extract_document(
        _real_pdf_bytes(),
        "doc.pdf",
        "application/pdf",
        endpoint=InferenceEndpoint(model="compatible/whatever", base_url="http://svc:8080"),
    )
    assert result.engine == "raw_text"


async def test_service_response_images_attached_to_correct_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def extract(self, data, filename, content_type, *, timeout_s=None):
            return ExtractResponse(
                success=True,
                pages=[
                    ExtractedPageText(page_number=1, text="p1", markdown="p1"),
                    ExtractedPageText(page_number=2, text="p2", markdown="p2"),
                ],
                images=[
                    ExtractedImage(
                        data_base64=base64.b64encode(b"img-bytes").decode(),
                        page_number=2,
                        label="chart",
                    )
                ],
                engine="ppstructurev3",
                page_count=2,
            )

    monkeypatch.setattr(extract_mod, "ExtractionClient", _FakeClient)

    result = await extract_mod.extract_document(
        b"data",
        "doc.pdf",
        "application/pdf",
        endpoint=InferenceEndpoint(model="compatible/whatever", base_url="http://svc:8080"),
    )
    assert result.pages[0].images == []
    assert len(result.pages[1].images) == 1
    assert result.pages[1].images[0].data == b"img-bytes"


async def test_unsupported_local_content_type_returns_empty_result() -> None:
    result = await extract_mod.extract_document(
        b"data", "archive.zip", "application/zip", endpoint=None
    )
    assert result.pages == []
