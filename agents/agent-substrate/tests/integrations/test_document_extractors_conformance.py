"""Every document extractor, held to the extractor conformance suite."""

from __future__ import annotations

import base64
import json

import httpx2 as httpx  # the service clients are built on httpx2, so the scripted transport must be too
import pytest

from substrate.integrations.document.local_extractor import LocalDocumentExtractor
from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.testing.conformance.document_extractor import DocumentExtractorConformance, pdf
from substrate.runtimes.document_intelligence import extract as extract_module
from substrate.runtimes.document_intelligence.client import ExtractionClient


class LocalPdf:
    paged = True
    rejects_garbage = True

    def extractor(self):
        return LocalDocumentExtractor(ocr=False)

    def document(self, pages):
        return pdf(pages), "doc.pdf"


class TestLocalDocumentExtractor(DocumentExtractorConformance):
    @pytest.fixture
    def provider(self):
        return LocalPdf()


class ScriptedService:
    """The document-intelligence service as an HTTP peer: a sample 'document' is JSON of its pages, and the
    scripted service answers with the wire shape the real one does (or an error for anything unreadable)."""

    paged = True
    rejects_garbage = True

    def __init__(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            try:
                pages = json.loads(base64.b64decode(body["content_base64"]))
                assert isinstance(pages, list) and pages
            except Exception:
                return httpx.Response(422, json={"detail": "unreadable document"})
            return httpx.Response(200, json={
                "success": True, "engine": "scripted-service", "page_count": len(pages),
                "text": "\n\n".join(pages), "markdown": "\n\n".join(pages),
                "pages": [{"page_number": i, "text": t, "markdown": t} for i, t in enumerate(pages, start=1)],
            })

        class Scripted(ExtractionClient):
            def _get_client(self):
                if self._client is None:
                    self._client = httpx.AsyncClient(base_url=self._base_url, transport=httpx.MockTransport(handler))
                return self._client

        monkeypatch.setattr(extract_module, "ExtractionClient", Scripted)

    def extractor(self):
        return extract_module.ServiceBackedDocumentExtractor(endpoint=InferenceEndpoint(base_url="http://docintel.invalid", model="docintel"))

    def document(self, pages):
        return json.dumps(pages).encode(), "doc.pdf"


class TestServiceBackedDocumentExtractor(DocumentExtractorConformance):
    @pytest.fixture
    def provider(self, monkeypatch):
        return ScriptedService(monkeypatch)
