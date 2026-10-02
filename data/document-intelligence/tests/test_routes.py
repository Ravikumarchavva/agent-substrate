"""Document-intelligence routes — the real built-in reader (isolated worker processes) and a fake layout engine on app.state, never the
real paddleocr model (that is test_pipeline.py's job). A bare FastAPI app with no lifespan, so nothing here loads the heavy extras."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from substrate.documents import ExtractedImage, ExtractedPage, ExtractionResult
from document_intelligence import convert
from document_intelligence.engines.native import NativeEngine
from document_intelligence.routes import router

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
DOCUMENTS = FIXTURES / "documents"


@dataclass
class _FakeConfig:
    auth_token: str = ""
    max_upload_bytes: int = 50 * 1024 * 1024
    pod_name: str = "document-intelligence-test"
    mode: str = "auto"
    enable_document_security_scan: bool = False


@dataclass
class _FakeResolved:
    mode: str = "ocr_classic"
    degraded_from: str | None = None
    worker_count: int = 1


class _FakeLayoutEngine:
    """A layout engine: answers PDFs and images with ``pages`` (or raises ``error``), and records what it was asked."""

    name = "fake-layout"

    def __init__(self, pages: list[ExtractedPage] | None = None, error: Exception | None = None) -> None:
        self._pages, self._error, self.calls = pages or [], error, []

    def accepts(self, filename: str, content_type: str) -> bool:
        return True

    def warmup(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def aextract(self, data: bytes, filename: str) -> ExtractionResult:
        self.calls.append(filename)
        if self._error is not None:
            raise self._error
        return ExtractionResult(pages=self._pages, markdown="\n\n".join(p.markdown or p.text for p in self._pages), engine=self.name)


def _client(*, engine=None, config=None, native=None) -> tuple[TestClient, object]:
    app = FastAPI()
    app.include_router(router)
    app.state.native = native or NativeEngine()
    app.state.engine = engine or app.state.native
    app.state.config = config or _FakeConfig()
    app.state.resolved = _FakeResolved()
    app.state.start_time = time.monotonic()
    return TestClient(app), app.state.engine


def _body(data: bytes, filename: str, content_type: str = "", strategy: str = "auto") -> dict:
    return {"content_base64": base64.b64encode(data).decode("ascii"), "filename": filename, "content_type": content_type, "strategy": strategy}


def _post(client: TestClient, data: bytes, filename: str, **kw) -> dict:
    resp = client.post("/v1/extract", json=_body(data, filename, **kw))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the answer is an ExtractionResult ────────────────────────────────────────


def test_a_pdf_is_read_and_answered_as_an_extraction_result():
    client, _ = _client()
    body = _post(client, (FIXTURES / "test_invoice.pdf").read_bytes(), "invoice.pdf", content_type="application/pdf")
    assert body["success"] is True and body["engine"] == "pdfium"
    assert "<!-- page 1 -->" in body["markdown"] and "Invoice #12345" in body["pages"][0]["text"]


def test_office_formats_are_read_natively_whatever_the_declared_type():
    client, _ = _client()
    for name in ("sample.docx", "sample.pptx", "sample.xlsx", "sample.odt"):
        body = _post(client, (DOCUMENTS / name).read_bytes(), name, content_type="application/octet-stream")
        assert body["success"] is True and body["engine"] == "native", (name, body)
        assert body["markdown"].startswith("<!-- page 1 -->")


def test_a_layout_engine_never_sees_an_office_document():
    client, engine = _client(engine=_FakeLayoutEngine(pages=[ExtractedPage(page_number=1, text="from layout")]))
    body = _post(client, (DOCUMENTS / "sample.docx").read_bytes(), "sample.docx", strategy="hi_res")
    assert body["engine"] == "native" and engine.calls == []


# ── choosing between the built-in reader and the layout engine ────────────────


def _layout_pages() -> list[ExtractedPage]:
    return [ExtractedPage(page_number=1, text="Invoice 4417", markdown="# Invoice 4417", images=[ExtractedImage(data=b"png", id="img-p1-0")])]


def test_hi_res_goes_to_the_layout_engine_and_its_markdown_gets_page_markers():
    client, engine = _client(engine=_FakeLayoutEngine(pages=_layout_pages()))
    body = _post(client, (FIXTURES / "test_invoice.pdf").read_bytes(), "i.pdf", strategy="hi_res")
    assert engine.calls == ["i.pdf"] and body["engine"] == "fake-layout"
    assert body["markdown"].startswith("<!-- page 1 -->") and body["pages"][0]["images"][0]["id"] == "img-p1-0"
    assert base64.b64decode(body["pages"][0]["images"][0]["data"]) == b"png"


def test_auto_keeps_the_built_in_answer_when_every_page_has_text():
    client, engine = _client(engine=_FakeLayoutEngine(pages=_layout_pages()))
    body = _post(client, (FIXTURES / "test_invoice.pdf").read_bytes(), "i.pdf")
    assert engine.calls == [] and body["engine"] == "pdfium"


def test_fast_never_uses_the_layout_engine():
    client, engine = _client(engine=_FakeLayoutEngine(pages=_layout_pages()))
    _post(client, (FIXTURES / "test_invoice.pdf").read_bytes(), "i.pdf", strategy="fast")
    assert engine.calls == []


def test_auto_escalates_a_scanned_page_to_the_layout_engine_when_the_built_in_reader_has_no_ocr():
    from substrate.documents import Reader

    client, engine = _client(engine=_FakeLayoutEngine(pages=_layout_pages()), native=NativeEngine(reader=Reader(ocr=None)))
    body = _post(client, (DOCUMENTS / "scanned_page.pdf").read_bytes(), "scan.pdf")
    assert engine.calls == ["scan.pdf"] and body["engine"] == "fake-layout"


def test_a_failing_layout_engine_degrades_to_the_built_in_reader_and_says_so():
    client, _ = _client(engine=_FakeLayoutEngine(error=RuntimeError("mkldnn boom")))
    body = _post(client, (FIXTURES / "test_invoice.pdf").read_bytes(), "i.pdf", strategy="hi_res")
    assert body["success"] is True and body["degraded_from"] == "fake-layout"
    assert any("mkldnn boom" in w for w in body["warnings"])


# ── what cannot be read is a failure with a reason, not an error ──────────────


def test_a_legacy_office_file_without_libreoffice_says_what_to_do(monkeypatch):
    async def _missing(data, filename, **kw):
        return None

    monkeypatch.setattr(convert, "convert_via_libreoffice", _missing)
    client, _ = _client()
    body = _post(client, b"\xd0\xcf\x11\xe0" + bytes(200), "old.doc")
    assert body["success"] is False and "LibreOffice" in body["error"] and "DOCX" in body["error"]


def test_a_legacy_office_file_is_converted_and_read_as_a_pdf(monkeypatch):
    seen = {}

    async def _convert(data, filename, **kw):
        seen["filename"] = filename
        return (FIXTURES / "test_invoice.pdf").read_bytes()

    monkeypatch.setattr(convert, "convert_via_libreoffice", _convert)
    client, _ = _client()
    body = _post(client, b"\xd0\xcf\x11\xe0" + bytes(200), "old.doc")
    assert seen["filename"] == "old.doc" and body["success"] is True and "Invoice #12345" in body["markdown"]


def test_garbage_is_a_failure_with_a_reason():
    client, _ = _client()
    body = _post(client, bytes(range(256)) * 20, "x.bin")
    assert body["success"] is False and body["error"]


def test_invalid_base64_returns_400():
    client, _ = _client()
    resp = client.post("/v1/extract", json={"content_base64": "not-valid-base64!!!", "filename": "t.pdf"})
    assert resp.status_code == 400


def test_oversized_file_returns_413():
    client, _ = _client(config=_FakeConfig(max_upload_bytes=4))
    assert client.post("/v1/extract", json=_body(b"way too big", "t.pdf")).status_code == 413


# ── /v1/health and auth ───────────────────────────────────────────────────────


def test_health_returns_ok():
    client, _ = _client(config=_FakeConfig(pod_name="document-intelligence-7"))
    body = client.get("/v1/health").json()
    assert body["status"] == "ok" and body["pod_name"] == "document-intelligence-7" and body["engine"] == "native"


def test_health_has_no_auth_requirement_even_when_token_configured():
    client, _ = _client(config=_FakeConfig(auth_token="secret"))
    assert client.get("/v1/health").status_code == 200


@pytest.mark.parametrize(("headers", "status"), [({}, 401), ({"Authorization": "Bearer wrong"}, 403), ({"Authorization": "Bearer secret"}, 200)])
def test_the_bearer_token_is_checked(headers, status):
    client, _ = _client(config=_FakeConfig(auth_token="secret"))
    resp = client.post("/v1/extract", json=_body((FIXTURES / "test_invoice.pdf").read_bytes(), "i.pdf"), headers=headers)
    assert resp.status_code == status
