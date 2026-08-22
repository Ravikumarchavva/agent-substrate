"""The document-security-scan gate in document-intelligence service routes.py::extract()
— real doc-firewall scan on real PDF bytes, exercised through the actual
FastAPI route (not just runtimes/document_intelligence/security_scan.py directly,
which test_security_scan.py already covers at the unit level).

Verifies the gate actually short-circuits BEFORE the (fake) pipeline runs —
a hostile file must never reach the parser, per the plan's ordering
requirement.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from substrate.runtimes.document_intelligence.service.pipeline import (
    ExtractedPage,
    ExtractionResult,
)
from substrate.runtimes.document_intelligence.service.routes import router


@dataclass
class _FakeConfig:
    auth_token: str = ""
    max_upload_bytes: int = 50 * 1024 * 1024
    pod_name: str = "document-intelligence-test"
    enable_document_security_scan: bool = True


class _FakePipeline:
    def __init__(self) -> None:
        self.extract_calls: list[bytes] = []

    def extract(self, data: bytes, filename: str) -> ExtractionResult:
        self.extract_calls.append(data)
        return ExtractionResult(
            pages=[ExtractedPage(page_number=1, text="parsed content", images=[])],
            markdown="parsed content",
        )


def _client(
    *, config: _FakeConfig | None = None, pipeline: _FakePipeline | None = None
):
    app = FastAPI()
    app.include_router(router)
    app.state.pipeline = pipeline or _FakePipeline()
    app.state.embedding_reranker = None
    app.state.config = config or _FakeConfig()
    app.state.start_time = time.monotonic()
    return TestClient(app), app.state.pipeline


def _malicious_pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_js('app.alert("test");')
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _clean_pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_malicious_pdf_is_rejected_before_reaching_the_parser():
    client, pipeline = _client()
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": base64.b64encode(_malicious_pdf_bytes()).decode(),
            "filename": "malicious.pdf",
            "content_type": "application/pdf",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert "security scan" in body["error"].lower()
    assert pipeline.extract_calls == []  # parser never ran


def test_clean_pdf_still_succeeds_through_the_scan_and_parser():
    client, pipeline = _client()
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": base64.b64encode(_clean_pdf_bytes()).decode(),
            "filename": "clean.pdf",
            "content_type": "application/pdf",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["text"] == "parsed content"
    assert len(pipeline.extract_calls) == 1  # parser did run


def test_scan_can_be_disabled_via_config():
    config = _FakeConfig(enable_document_security_scan=False)
    client, pipeline = _client(config=config)
    resp = client.post(
        "/v1/extract",
        json={
            "content_base64": base64.b64encode(_malicious_pdf_bytes()).decode(),
            "filename": "malicious.pdf",
            "content_type": "application/pdf",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True  # scan skipped, parser ran unguarded
    assert len(pipeline.extract_calls) == 1
