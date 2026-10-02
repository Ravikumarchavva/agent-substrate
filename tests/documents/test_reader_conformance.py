"""``Reader`` held to the extractor conformance suite three ways: in a worker process, in a thread, and by URL (a stdlib HTTP server
answering with a ``Reader`` — the same wire as the document-intelligence app)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from substrate.documents import Reader
from substrate.testing.conformance.document_extractor import DocumentExtractorConformance, pdf
from tests.documents._files import fixture


class _Provider:
    paged = True
    rejects_garbage = True

    def __init__(self, reader: Reader) -> None:
        self._reader = reader

    def extractor(self) -> Reader:
        return self._reader

    def document(self, pages):
        return pdf(pages), "doc.pdf"

    def scanned(self):
        return fixture("scanned_page.pdf"), "scan.pdf"


class TestIsolatedReader(DocumentExtractorConformance):
    @pytest.fixture
    def provider(self):
        return _Provider(Reader())


class TestInProcessReader(DocumentExtractorConformance):
    @pytest.fixture
    def provider(self):
        return _Provider(Reader(isolate=False))


class _Handler(BaseHTTPRequestHandler):
    reader = Reader(isolate=False)

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_POST(self) -> None:  # noqa: N802
        import asyncio
        import base64

        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        result = asyncio.run(
            self.reader.read(base64.b64decode(body["content_base64"]), body["filename"], content_type=body["content_type"] or None, strategy=body["strategy"])
        )
        payload = result.model_dump_json().encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def document_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class TestReaderByUrl(DocumentExtractorConformance):
    @pytest.fixture
    def provider(self, document_server):
        return _Provider(Reader(document_server, fallback=False))
